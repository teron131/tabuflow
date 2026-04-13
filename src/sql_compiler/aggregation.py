"""Aggregation checks for deterministic SQL compiler inputs."""

from __future__ import annotations

from collections.abc import Callable

from .renderer import render_column_reference
from .types import CompileContext, SQLCompileIssue, SQLExpression, SQLQuery, SQLSelectItem
from .validator import append_issue, is_aggregate_function_name

RenderExpressionFn = Callable[..., str]


def expression_contains_aggregate(expression: SQLExpression) -> bool:
    """Return whether one expression tree contains an aggregate function."""
    if expression.kind == "function":
        if is_aggregate_function_name(expression.function_name):
            return True
        return any(expression_contains_aggregate(argument) for argument in expression.arguments)
    if expression.kind == "binary_op":
        return (expression.left is not None and expression_contains_aggregate(expression.left)) or (
            expression.right is not None and expression_contains_aggregate(expression.right)
        )
    if expression.kind == "unary_op":
        return expression.operand is not None and expression_contains_aggregate(expression.operand)
    if expression.kind == "list":
        return any(expression_contains_aggregate(item) for item in expression.items)
    if expression.kind == "case":
        return (
            any(expression_contains_aggregate(condition) for condition in expression.conditions)
            or any(expression_contains_aggregate(result) for result in expression.results)
            or (expression.else_expression is not None and expression_contains_aggregate(expression.else_expression))
        )
    return False


def query_has_aggregate(query: SQLQuery) -> bool:
    """Return whether one query uses aggregate functions in aggregate-aware clauses."""
    candidate_expressions = [item.expression for item in query.select_items if item.expression is not None]
    candidate_expressions.extend(item.expression for item in query.order_by_items if item.expression is not None)
    if query.having is not None:
        candidate_expressions.append(query.having)
    return any(expression_contains_aggregate(expression) for expression in candidate_expressions)


def validate_where_aggregation(
    query: SQLQuery,
    *,
    field_prefix: str,
    issues: list[SQLCompileIssue],
) -> None:
    """Reject aggregate functions inside WHERE clauses."""
    if query.where is None or not expression_contains_aggregate(query.where):
        return
    append_issue(
        issues,
        code="invalid_aggregation",
        field=f"{field_prefix}.where",
        value=None,
        message="WHERE clauses must not contain aggregate functions. Use HAVING after grouping instead.",
    )


def validate_group_by_aggregation(
    query: SQLQuery,
    *,
    field_prefix: str,
    issues: list[SQLCompileIssue],
) -> None:
    """Reject aggregate functions inside GROUP BY expressions."""
    for group_idx, group_item in enumerate(query.group_by_items):
        if not expression_contains_aggregate(group_item):
            continue
        append_issue(
            issues,
            code="invalid_aggregation",
            field=f"{field_prefix}.group_by_items[{group_idx}]",
            value=None,
            message="GROUP BY expressions must not contain aggregate functions.",
        )


def render_group_by_expressions(
    query: SQLQuery,
    *,
    context: CompileContext,
    render_expression_fn: RenderExpressionFn,
) -> set[str]:
    """Render GROUP BY expressions for exact aggregate-shape matching."""
    return {render_expression_fn(item, context=context) for item in query.group_by_items}


def grouped_column_references(query: SQLQuery) -> set[str]:
    """Return grouped plain column references for aggregate validation."""
    return {
        render_column_reference(
            group_item.column_name or "",
            source_alias=group_item.source_alias,
        )
        for group_item in query.group_by_items
        if group_item.kind == "column"
    }


def validate_grouped_select_item(
    select_item: SQLSelectItem,
    *,
    item_idx: int,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    context: CompileContext,
    grouped_columns: set[str],
    rendered_group_by: set[str],
    render_expression_fn: RenderExpressionFn,
) -> None:
    """Validate one projected item in an aggregate query."""
    field_base = f"{field_prefix}.select_items[{item_idx}]"
    if select_item.column_name is not None:
        rendered_column = render_column_reference(select_item.column_name, source_alias=select_item.source_alias)
        if rendered_column not in grouped_columns:
            append_issue(
                issues,
                code="invalid_aggregation",
                field=f"{field_base}.column_name",
                value=rendered_column,
                message=f"Non-aggregate select column must appear in GROUP BY: {rendered_column}",
            )
        return

    if select_item.expression is None or expression_contains_aggregate(select_item.expression):
        return

    rendered_expression = render_expression_fn(select_item.expression, context=context)
    if rendered_expression not in rendered_group_by:
        append_issue(
            issues,
            code="invalid_aggregation",
            field=f"{field_base}.expression",
            value=rendered_expression,
            message="Non-aggregate select expressions must appear exactly in GROUP BY when aggregate functions are present.",
        )


def validate_aggregation_shape(
    query: SQLQuery,
    *,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    context: CompileContext,
    render_expression_fn: RenderExpressionFn,
) -> None:
    """Reject aggregate query shapes that are not deterministic."""
    validate_where_aggregation(query, field_prefix=field_prefix, issues=issues)
    validate_group_by_aggregation(query, field_prefix=field_prefix, issues=issues)
    if not query_has_aggregate(query):
        return

    rendered_group_by = render_group_by_expressions(
        query,
        context=context,
        render_expression_fn=render_expression_fn,
    )
    grouped_columns = grouped_column_references(query)

    for item_idx, select_item in enumerate(query.select_items):
        validate_grouped_select_item(
            select_item,
            item_idx=item_idx,
            field_prefix=field_prefix,
            issues=issues,
            context=context,
            grouped_columns=grouped_columns,
            rendered_group_by=rendered_group_by,
            render_expression_fn=render_expression_fn,
        )
