"""Compilation flow for deterministic SQL plans."""

from __future__ import annotations

from collections.abc import Collection

from .aggregation import validate_aggregation_shape
from .expression_validator import validate_expression
from .normalizer import normalize_compile_input
from .parser import SQLImportError, import_compile_input
from .projections import validate_and_render_order_limit_offset, validate_and_render_select_items
from .renderer import indent_sql, render_expression
from .sources import resolve_from_clause
from .types import (
    CompileContext,
    CompiledQuery,
    QueryScope,
    SQLCompileInput,
    SQLCompileResult,
    SQLExpression,
    SQLOrderByItem,
    SQLQuery,
    SQLSelectItem,
)
from .validator import append_issue, extend_scope_with_output_names, is_safe_identifier

SET_OPERATOR_SQL = {
    "union": "UNION",
    "union_all": "UNION ALL",
    "intersect": "INTERSECT",
    "except": "EXCEPT",
}


def render_validated_expression(expression: SQLExpression, *, context: CompileContext) -> str:
    """Render one validated SQL expression using the local query compiler for subqueries."""
    return render_expression(
        expression,
        context=context,
        compile_query_fn=lambda query: compile_query(
            query,
            issues=[],
            field_prefix="render.subquery",
            context=context,
        ),
    )


def validate_query_clauses(
    query: SQLQuery,
    *,
    field_prefix: str,
    issues: list,
    context: CompileContext,
    source_scope: QueryScope,
    selectable_scope: QueryScope,
) -> None:
    """Validate aggregate, filter, and grouping clauses for one simple query."""
    validate_aggregation_shape(
        query,
        field_prefix=field_prefix,
        issues=issues,
        context=context,
        render_expression_fn=render_validated_expression,
    )

    if query.where is not None:
        validate_expression(
            query.where,
            field_prefix=f"{field_prefix}.where",
            issues=issues,
            scope=source_scope,
            context=context,
            compile_query_fn=compile_query,
            render_expression_fn=render_validated_expression,
        )

    for group_idx, group_item in enumerate(query.group_by_items):
        validate_expression(
            group_item,
            field_prefix=f"{field_prefix}.group_by_items[{group_idx}]",
            issues=issues,
            scope=source_scope,
            context=context,
            compile_query_fn=compile_query,
            render_expression_fn=render_validated_expression,
        )

    if query.having is None:
        return

    validate_expression(
        query.having,
        field_prefix=f"{field_prefix}.having",
        issues=issues,
        scope=selectable_scope,
        context=context,
        compile_query_fn=compile_query,
        render_expression_fn=render_validated_expression,
    )


def render_simple_query_sql(
    query: SQLQuery,
    *,
    source_sql: str,
    rendered_select_items: list[str],
    context: CompileContext,
    trailing_lines: list[str],
) -> str:
    """Render one validated non-compound query block."""
    select_prefix = "SELECT DISTINCT" if query.distinct else "SELECT"
    sql_lines = [
        select_prefix,
        ",\n".join(f"    {item_sql}" for item_sql in rendered_select_items),
        f"FROM {source_sql}",
    ]

    if query.where is not None:
        sql_lines.append(f"WHERE {render_validated_expression(query.where, context=context)}")
    if query.group_by_items:
        rendered_group_by = ", ".join(render_validated_expression(item, context=context) for item in query.group_by_items)
        sql_lines.append(f"GROUP BY {rendered_group_by}")
    if query.having is not None:
        sql_lines.append(f"HAVING {render_validated_expression(query.having, context=context)}")

    sql_lines.extend(trailing_lines)
    return "\n".join(sql_lines)


def compile_simple_query(
    query: SQLQuery,
    *,
    issues: list,
    field_prefix: str,
    context: CompileContext,
) -> CompiledQuery:
    """Compile one non-compound query block."""
    source_sql, source_scope = resolve_from_clause(
        query,
        issues=issues,
        field_prefix=field_prefix,
        context=context,
        compile_query_fn=compile_query,
        validate_expression_fn=validate_expression,
        render_expression_fn=render_validated_expression,
    )

    if not query.select_items:
        append_issue(
            issues,
            code="empty_select",
            field=f"{field_prefix}.select_items",
            value=None,
            message="Compiler input must contain at least one select item.",
        )

    select_projection = validate_and_render_select_items(
        query.select_items,
        field_prefix=field_prefix,
        issues=issues,
        context=context,
        scope=source_scope,
        compile_query_fn=compile_query,
        validate_expression_fn=validate_expression,
        render_expression_fn=render_validated_expression,
    )
    selectable_scope = extend_scope_with_output_names(
        source_scope,
        select_projection.selectable_names,
    )
    validate_query_clauses(
        query,
        field_prefix=field_prefix,
        issues=issues,
        context=context,
        source_scope=source_scope,
        selectable_scope=selectable_scope,
    )
    trailing_lines = validate_and_render_order_limit_offset(
        query,
        field_prefix=field_prefix,
        issues=issues,
        context=context,
        scope=selectable_scope,
        compile_query_fn=compile_query,
        validate_expression_fn=validate_expression,
        render_expression_fn=render_validated_expression,
    )

    return CompiledQuery(
        sql=render_simple_query_sql(
            query,
            source_sql=source_sql,
            rendered_select_items=select_projection.rendered_items,
            context=context,
            trailing_lines=trailing_lines,
        ),
        output_names=select_projection.output_names,
        selectable_names=select_projection.selectable_names,
        projected_column_count=len(select_projection.rendered_items),
    )


def compound_branch_query(query: SQLQuery) -> SQLQuery:
    """Return one branch-safe query without outer compound trailing clauses."""
    return query.model_copy(
        update={
            "order_by_items": [],
            "limit": None,
            "offset": None,
            "set_operations": [],
        }
    )


def validate_compound_branch(
    branch_query: SQLQuery,
    *,
    branch_prefix: str,
    operator: str,
    issues: list,
) -> None:
    """Reject set-operation branches with unsupported trailing clauses."""
    if branch_query.order_by_items or branch_query.limit is not None or branch_query.offset is not None:
        append_issue(
            issues,
            code="invalid_set_operation",
            field=branch_prefix,
            value=operator,
            message="Set-operation branches must not include ORDER BY, LIMIT, or OFFSET. Apply them to the compound query instead.",
        )


def compound_output_scope(base_compiled: CompiledQuery) -> QueryScope:
    """Build the scope exposed by a compound query output."""
    return QueryScope(
        unqualified_names=set(base_compiled.output_names),
        ambiguous_names=set(),
        qualified_names={},
    )


def compile_query(
    query: SQLQuery,
    *,
    issues: list,
    field_prefix: str,
    context: CompileContext,
) -> CompiledQuery:
    """Compile one structured query block recursively."""
    if not query.set_operations:
        return compile_simple_query(
            query,
            issues=issues,
            field_prefix=field_prefix,
            context=context,
        )

    base_compiled = compile_simple_query(
        compound_branch_query(query),
        issues=issues,
        field_prefix=field_prefix,
        context=context,
    )
    compound_parts = [base_compiled.sql]

    for branch_idx, set_operation in enumerate(query.set_operations):
        branch_prefix = f"{field_prefix}.set_operations[{branch_idx}]"
        branch_query = set_operation.query
        validate_compound_branch(
            branch_query,
            branch_prefix=branch_prefix,
            operator=set_operation.operator,
            issues=issues,
        )

        compiled_branch = compile_simple_query(
            compound_branch_query(branch_query),
            issues=issues,
            field_prefix=f"{branch_prefix}.query",
            context=context,
        )
        if compiled_branch.projected_column_count != base_compiled.projected_column_count:
            append_issue(
                issues,
                code="invalid_set_operation",
                field=branch_prefix,
                value=set_operation.operator,
                message="Each compound-query branch must project the same number of output columns.",
            )

        compound_parts.extend([SET_OPERATOR_SQL[set_operation.operator], compiled_branch.sql])

    trailing_lines = validate_and_render_order_limit_offset(
        query,
        field_prefix=field_prefix,
        issues=issues,
        context=context,
        scope=compound_output_scope(base_compiled),
        compile_query_fn=compile_query,
        validate_expression_fn=validate_expression,
        render_expression_fn=render_validated_expression,
    )
    return CompiledQuery(
        sql="\n".join(compound_parts + trailing_lines),
        output_names=base_compiled.output_names,
        selectable_names=set(base_compiled.output_names),
        projected_column_count=base_compiled.projected_column_count,
    )


def build_compile_input(
    target_name: str,
    *,
    select_columns: list[str],
    order_by_columns: list[str],
) -> SQLCompileInput:
    """Build compiler input from a simple column-only plan."""
    return SQLCompileInput(
        target_name=target_name,
        select_items=[SQLSelectItem(column_name=column_name) for column_name in select_columns],
        order_by_items=[SQLOrderByItem(column_name=column_name) for column_name in order_by_columns],
    )


def compile_common_table_expressions(
    compile_input: SQLCompileInput,
    *,
    issues: list,
    context: CompileContext,
) -> list[str]:
    """Compile and register deterministic CTE outputs."""
    rendered_ctes: list[str] = []
    seen_cte_names: set[str] = set()

    for cte_idx, cte in enumerate(compile_input.ctes):
        field_base = f"ctes[{cte_idx}]"
        if not is_safe_identifier(cte.name):
            append_issue(
                issues,
                code="unsafe_cte_name",
                field=f"{field_base}.name",
                value=cte.name,
                message=f"Unsafe SQLite CTE identifier: {cte.name}",
            )
            continue
        if cte.name in seen_cte_names:
            append_issue(
                issues,
                code="duplicate_cte_name",
                field=f"{field_base}.name",
                value=cte.name,
                message=f"Duplicate CTE name: {cte.name}",
            )
            continue

        seen_cte_names.add(cte.name)
        compiled_cte = compile_query(
            cte.query,
            issues=issues,
            field_prefix=f"{field_base}.query",
            context=context,
        )
        context.cte_outputs[cte.name] = compiled_cte.selectable_names
        rendered_ctes.append(f"{cte.name} AS (\n{indent_sql(compiled_cte.sql)}\n)")

    return rendered_ctes


def compile_sql(
    compile_input: SQLCompileInput | SQLQuery | str,
    *,
    available_columns: Collection[str] | None = None,
) -> SQLCompileResult:
    """Compile one structured query shape into deterministic SQLite SQL."""
    try:
        structured_input = import_compile_input(compile_input)
    except SQLImportError as exc:
        empty_input = SQLCompileInput(target_name="")
        return SQLCompileResult(
            status="error",
            normalized=empty_input,
            sql=None,
            issues=[exc.to_issue()],
        )

    normalized = normalize_compile_input(structured_input)
    issues: list = []
    authoritative_columns = None
    if available_columns is not None:
        authoritative_columns = {column.strip() for column in available_columns}

    if not is_safe_identifier(normalized.target_name):
        append_issue(
            issues,
            code="unsafe_target",
            field="target_name",
            value=normalized.target_name,
            message=f"Unsafe SQLite target identifier: {normalized.target_name}",
        )

    context = CompileContext(
        authoritative_target_name=normalized.target_name,
        authoritative_columns=authoritative_columns,
        cte_outputs={},
    )
    rendered_ctes = compile_common_table_expressions(
        normalized,
        issues=issues,
        context=context,
    )
    compiled_root = compile_query(
        normalized,
        issues=issues,
        field_prefix="root",
        context=context,
    )
    if issues:
        return SQLCompileResult(status="error", normalized=normalized, sql=None, issues=issues)

    sql = compiled_root.sql
    if rendered_ctes:
        sql = f"WITH {',\n'.join(rendered_ctes)}\n{sql}"
    return SQLCompileResult(status="ok", normalized=normalized, sql=sql, issues=issues)
