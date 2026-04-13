"""SQL rendering helpers for the deterministic compiler."""

from __future__ import annotations

from collections.abc import Callable

from .types import CompileContext, CompiledQuery, SQLExpression, SQLOrderByItem, SQLQuery, SQLSelectItem

CompileSubqueryFn = Callable[[SQLQuery], CompiledQuery]


def render_literal(value: str | int | float | bool | None) -> str:
    """Render one SQL literal value."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    escaped_value = value.replace("'", "''")
    return f"'{escaped_value}'"


def render_column_reference(column_name: str, *, source_alias: str | None = None) -> str:
    """Render one plain or qualified column reference."""
    if source_alias:
        return f"{source_alias}.{column_name}"
    return column_name


def render_source_sql(source_sql: str, *, alias: str | None = None) -> str:
    """Render one FROM source with an optional alias."""
    if alias:
        return f"{source_sql} AS {alias}"
    return source_sql


def render_select_item(item: SQLSelectItem, *, expression_sql: str | None = None) -> str:
    """Render one validated select item."""
    base_sql = expression_sql or render_column_reference(item.column_name or "", source_alias=item.source_alias)
    if item.alias and item.alias != base_sql:
        return f"{base_sql} AS {item.alias}"
    return base_sql


def render_order_by_item(item: SQLOrderByItem, *, expression_sql: str | None = None) -> str:
    """Render one validated order-by item."""
    base_sql = expression_sql or render_column_reference(item.column_name or "", source_alias=item.source_alias)
    if item.direction == "desc":
        return f"{base_sql} DESC"
    return base_sql


def indent_sql(sql: str, *, spaces: int = 4) -> str:
    """Indent one SQL fragment."""
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else line for line in sql.splitlines())


def render_expression(
    expression: SQLExpression,
    *,
    context: CompileContext,
    compile_query_fn: CompileSubqueryFn,
) -> str:
    """Render one validated SQL expression recursively."""
    if expression.kind == "column":
        return render_column_reference(expression.column_name or "", source_alias=expression.source_alias)
    if expression.kind == "star":
        return "*"
    if expression.kind == "literal":
        return render_literal(expression.value)
    if expression.kind == "function":
        arguments = ", ".join(render_expression(argument, context=context, compile_query_fn=compile_query_fn) for argument in expression.arguments)
        return f"{expression.function_name}({arguments})"
    if expression.kind == "binary_op":
        left_sql = render_expression(expression.left or SQLExpression(kind="literal", value=None), context=context, compile_query_fn=compile_query_fn)
        right_expression = expression.right or SQLExpression(kind="literal", value=None)
        if expression.operator in {"BETWEEN", "NOT BETWEEN"} and right_expression.kind == "list" and len(right_expression.items) == 2:
            lower_sql = render_expression(right_expression.items[0], context=context, compile_query_fn=compile_query_fn)
            upper_sql = render_expression(right_expression.items[1], context=context, compile_query_fn=compile_query_fn)
            return f"({left_sql} {expression.operator} {lower_sql} AND {upper_sql})"
        right_sql = render_expression(right_expression, context=context, compile_query_fn=compile_query_fn)
        return f"({left_sql} {expression.operator} {right_sql})"
    if expression.kind == "unary_op":
        operand_sql = render_expression(expression.operand or SQLExpression(kind="literal", value=None), context=context, compile_query_fn=compile_query_fn)
        return f"({expression.operator} {operand_sql})"
    if expression.kind == "list":
        rendered_items = ", ".join(render_expression(item, context=context, compile_query_fn=compile_query_fn) for item in expression.items)
        return f"({rendered_items})"
    if expression.kind == "subquery":
        nested_query = compile_query_fn(expression.query or SQLQuery())
        return f"(\n{indent_sql(nested_query.sql)}\n)"
    if expression.kind == "case":
        case_lines = ["CASE"]
        for condition, result in zip(expression.conditions, expression.results, strict=False):
            condition_sql = render_expression(condition, context=context, compile_query_fn=compile_query_fn)
            result_sql = render_expression(result, context=context, compile_query_fn=compile_query_fn)
            case_lines.append(f"    WHEN {condition_sql} THEN {result_sql}")
        if expression.else_expression is not None:
            else_sql = render_expression(expression.else_expression, context=context, compile_query_fn=compile_query_fn)
            case_lines.append(f"    ELSE {else_sql}")
        case_lines.append("END")
        return "\n".join(case_lines)
    raise ValueError(f"Unsupported expression kind during rendering: {expression.kind}")
