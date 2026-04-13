"""Projection validation and rendering for deterministic SQL compiler queries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .renderer import render_column_reference, render_order_by_item, render_select_item
from .types import (
    CompileContext,
    CompiledQuery,
    QueryScope,
    SQLCompileIssue,
    SQLOrderByItem,
    SQLQuery,
    SQLSelectItem,
)
from .validator import (
    append_issue,
    expression_or_null,
    is_safe_identifier,
    validate_column_reference,
)

CompileQueryFn = Callable[[SQLQuery, list[SQLCompileIssue], str, CompileContext], CompiledQuery]
RenderExpressionFn = Callable[..., str]
ValidateExpressionFn = Callable[..., None]


@dataclass(slots=True)
class SelectProjection:
    """Rendered and named select outputs for one query block."""

    rendered_items: list[str]
    output_names: list[str]
    selectable_names: set[str]


def validate_and_render_order_limit_offset(
    query: SQLQuery,
    *,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    context: CompileContext,
    scope: QueryScope,
    compile_query_fn: CompileQueryFn,
    validate_expression_fn: ValidateExpressionFn,
    render_expression_fn: RenderExpressionFn,
) -> list[str]:
    """Validate and render trailing ORDER BY/LIMIT/OFFSET clauses."""
    if query.limit is not None and query.limit < 0:
        append_issue(
            issues,
            code="invalid_limit",
            field=f"{field_prefix}.limit",
            value=str(query.limit),
            message="LIMIT must be zero or greater.",
        )
    if query.offset is not None and query.offset < 0:
        append_issue(
            issues,
            code="invalid_offset",
            field=f"{field_prefix}.offset",
            value=str(query.offset),
            message="OFFSET must be zero or greater.",
        )

    seen_order_by_columns: set[str] = set()
    rendered_order_by_items: list[str] = []
    for item_idx, order_by_item in enumerate(query.order_by_items):
        rendered_order_by_item = validate_and_render_order_by_item(
            order_by_item,
            item_idx=item_idx,
            field_prefix=field_prefix,
            issues=issues,
            context=context,
            scope=scope,
            seen_order_by_columns=seen_order_by_columns,
            compile_query_fn=compile_query_fn,
            validate_expression_fn=validate_expression_fn,
            render_expression_fn=render_expression_fn,
        )
        if rendered_order_by_item is not None:
            rendered_order_by_items.append(rendered_order_by_item)

    trailing_lines: list[str] = []
    if rendered_order_by_items:
        trailing_lines.append(f"ORDER BY {', '.join(rendered_order_by_items)}")
    if query.limit is not None:
        trailing_lines.append(f"LIMIT {query.limit}")
    if query.offset is not None:
        trailing_lines.append(f"OFFSET {query.offset}")
    return trailing_lines


def validate_and_render_order_by_item(
    order_by_item: SQLOrderByItem,
    *,
    item_idx: int,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    context: CompileContext,
    scope: QueryScope,
    seen_order_by_columns: set[str],
    compile_query_fn: CompileQueryFn,
    validate_expression_fn: ValidateExpressionFn,
    render_expression_fn: RenderExpressionFn,
) -> str | None:
    """Validate and render one ORDER BY item."""
    field_base = f"{field_prefix}.order_by_items[{item_idx}]"
    has_column = order_by_item.column_name is not None
    has_expression = order_by_item.expression is not None
    if has_column == has_expression:
        append_issue(
            issues,
            code="invalid_expression",
            field=field_base,
            value=None,
            message="ORDER BY items must provide exactly one of column_name or expression.",
        )
        return None

    rendered_expression: str | None = None
    if has_column:
        validate_column_reference(
            column_name=order_by_item.column_name or "",
            source_alias=order_by_item.source_alias,
            field_prefix=field_base,
            issues=issues,
            unsafe_code="unsafe_order_by_column",
            missing_code="missing_order_by_column",
            scope=scope,
        )
        dedupe_key = rendered_order_by_column(order_by_item)
        if dedupe_key is not None:
            if dedupe_key in seen_order_by_columns:
                append_issue(
                    issues,
                    code="duplicate_order_by_column",
                    field=f"{field_base}.column_name",
                    value=dedupe_key,
                    message=f"Duplicate ORDER BY column: {dedupe_key}",
                )
            else:
                seen_order_by_columns.add(dedupe_key)
    else:
        expression = expression_or_null(order_by_item.expression)
        validate_expression_fn(
            expression,
            field_prefix=f"{field_base}.expression",
            issues=issues,
            scope=scope,
            context=context,
            compile_query_fn=compile_query_fn,
            render_expression_fn=render_expression_fn,
        )
        rendered_expression = render_expression_fn(expression, context=context)

    return render_order_by_item(order_by_item, expression_sql=rendered_expression)


def rendered_order_by_column(order_by_item: SQLOrderByItem) -> str | None:
    """Return the rendered ORDER BY column when it is safe to dedupe."""
    if not is_safe_identifier(order_by_item.column_name or ""):
        return None
    return render_column_reference(
        order_by_item.column_name or "",
        source_alias=order_by_item.source_alias,
    )


def validate_and_render_select_items(
    select_items: list[SQLSelectItem],
    *,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    context: CompileContext,
    scope: QueryScope,
    compile_query_fn: CompileQueryFn,
    validate_expression_fn: ValidateExpressionFn,
    render_expression_fn: RenderExpressionFn,
) -> SelectProjection:
    """Validate and render SELECT items while tracking output names."""
    output_names: list[str] = []
    selectable_names: set[str] = set()
    seen_select_outputs: set[str] = set()
    rendered_select_items: list[str] = []

    for item_idx, select_item in enumerate(select_items):
        rendered_select_item = validate_and_render_select_item(
            select_item,
            item_idx=item_idx,
            field_prefix=field_prefix,
            issues=issues,
            context=context,
            scope=scope,
            seen_select_outputs=seen_select_outputs,
            output_names=output_names,
            selectable_names=selectable_names,
            compile_query_fn=compile_query_fn,
            validate_expression_fn=validate_expression_fn,
            render_expression_fn=render_expression_fn,
        )
        if rendered_select_item is not None:
            rendered_select_items.append(rendered_select_item)

    return SelectProjection(
        rendered_items=rendered_select_items,
        output_names=output_names,
        selectable_names=selectable_names,
    )


def validate_and_render_select_item(
    select_item: SQLSelectItem,
    *,
    item_idx: int,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    context: CompileContext,
    scope: QueryScope,
    seen_select_outputs: set[str],
    output_names: list[str],
    selectable_names: set[str],
    compile_query_fn: CompileQueryFn,
    validate_expression_fn: ValidateExpressionFn,
    render_expression_fn: RenderExpressionFn,
) -> str | None:
    """Validate and render one SELECT item."""
    field_base = f"{field_prefix}.select_items[{item_idx}]"
    has_column = select_item.column_name is not None
    has_expression = select_item.expression is not None
    if has_column == has_expression:
        append_issue(
            issues,
            code="invalid_expression",
            field=field_base,
            value=None,
            message="Select items must provide exactly one of column_name or expression.",
        )
        return None

    if select_item.alias is not None and not is_safe_identifier(select_item.alias):
        append_issue(
            issues,
            code="unsafe_select_alias",
            field=f"{field_base}.alias",
            value=select_item.alias,
            message=f"Unsafe SQLite select alias identifier: {select_item.alias}",
        )

    rendered_expression: str | None = None
    output_name = select_item.alias
    if has_column:
        validate_column_reference(
            column_name=select_item.column_name or "",
            source_alias=select_item.source_alias,
            field_prefix=field_base,
            issues=issues,
            unsafe_code="unsafe_select_column",
            missing_code="missing_select_column",
            scope=scope,
        )
        output_name = output_name or (select_item.column_name or None)
    else:
        expression = expression_or_null(select_item.expression)
        validate_expression_fn(
            expression,
            field_prefix=f"{field_base}.expression",
            issues=issues,
            scope=scope,
            context=context,
            compile_query_fn=compile_query_fn,
            render_expression_fn=render_expression_fn,
        )
        rendered_expression = render_expression_fn(expression, context=context)

    register_select_output(
        select_item,
        field_base=field_base,
        issues=issues,
        output_name=output_name,
        seen_select_outputs=seen_select_outputs,
        output_names=output_names,
        selectable_names=selectable_names,
    )
    return render_select_item(select_item, expression_sql=rendered_expression)


def register_select_output(
    select_item: SQLSelectItem,
    *,
    field_base: str,
    issues: list[SQLCompileIssue],
    output_name: str | None,
    seen_select_outputs: set[str],
    output_names: list[str],
    selectable_names: set[str],
) -> None:
    """Track select output names and selectable aliases."""
    if output_name is not None:
        if output_name in seen_select_outputs:
            append_issue(
                issues,
                code="duplicate_select_output",
                field=field_base,
                value=output_name,
                message=f"Duplicate select output name: {output_name}",
            )
        else:
            seen_select_outputs.add(output_name)
            selectable_names.add(output_name)
            output_names.append(output_name)

    if select_item.alias and is_safe_identifier(select_item.alias):
        selectable_names.add(select_item.alias)
    if select_item.column_name and is_safe_identifier(select_item.column_name):
        selectable_names.add(select_item.column_name)
