"""Normalization helpers for deterministic SQL compiler inputs."""

from __future__ import annotations

from .types import (
    SQLCommonTableExpression,
    SQLCompileInput,
    SQLExpression,
    SQLJoin,
    SQLOrderByItem,
    SQLQuery,
    SQLSelectItem,
    SQLSetOperation,
    SQLSource,
)


def normalize_identifier(identifier: str) -> str:
    """Trim one SQL identifier-like value."""
    return identifier.strip()


def normalize_optional_identifier(value: str | None) -> str | None:
    """Strip one optional SQL identifier, returning None if empty."""
    return None if value is None else normalize_identifier(value) or None


def normalize_expression(expression: SQLExpression) -> SQLExpression:
    """Return one normalized structured expression."""
    return SQLExpression(
        kind=expression.kind,
        column_name=normalize_optional_identifier(expression.column_name),
        source_alias=normalize_optional_identifier(expression.source_alias),
        value=expression.value,
        function_name=normalize_optional_identifier(expression.function_name),
        arguments=[normalize_expression(argument) for argument in expression.arguments],
        operator=expression.operator,
        left=None if expression.left is None else normalize_expression(expression.left),
        right=None if expression.right is None else normalize_expression(expression.right),
        operand=None if expression.operand is None else normalize_expression(expression.operand),
        query=None if expression.query is None else normalize_query(expression.query),
        items=[normalize_expression(item) for item in expression.items],
        conditions=[normalize_expression(condition) for condition in expression.conditions],
        results=[normalize_expression(result) for result in expression.results],
        else_expression=None if expression.else_expression is None else normalize_expression(expression.else_expression),
    )


def normalize_source(source: SQLSource) -> SQLSource:
    """Return one normalized FROM source."""
    return SQLSource(
        kind=source.kind,
        target_name=normalize_optional_identifier(source.target_name),
        cte_name=normalize_optional_identifier(source.cte_name),
        query=None if source.query is None else normalize_query(source.query),
        alias=normalize_optional_identifier(source.alias),
    )


def normalize_join(join: SQLJoin) -> SQLJoin:
    """Return one normalized join edge."""
    return SQLJoin(
        join_type=join.join_type,
        source=normalize_source(join.source),
        on=None if join.on is None else normalize_expression(join.on),
    )


def normalize_select_item(item: SQLSelectItem) -> SQLSelectItem:
    """Return one normalized select item."""
    return SQLSelectItem(
        column_name=normalize_optional_identifier(item.column_name),
        source_alias=normalize_optional_identifier(item.source_alias),
        expression=None if item.expression is None else normalize_expression(item.expression),
        alias=normalize_optional_identifier(item.alias),
    )


def normalize_order_by_item(item: SQLOrderByItem) -> SQLOrderByItem:
    """Return one normalized order-by item."""
    return SQLOrderByItem(
        column_name=normalize_optional_identifier(item.column_name),
        source_alias=normalize_optional_identifier(item.source_alias),
        expression=None if item.expression is None else normalize_expression(item.expression),
        direction=item.direction,
    )


def normalize_set_operation(set_operation: SQLSetOperation) -> SQLSetOperation:
    """Return one normalized compound branch."""
    return SQLSetOperation(
        operator=set_operation.operator,
        query=normalize_query(set_operation.query),
    )


def normalize_query(query: SQLQuery) -> SQLQuery:
    """Return one normalized query block."""
    return SQLQuery(
        source=None if query.source is None else normalize_source(query.source),
        joins=[normalize_join(join) for join in query.joins],
        select_items=[normalize_select_item(item) for item in query.select_items],
        where=None if query.where is None else normalize_expression(query.where),
        group_by_items=[normalize_expression(item) for item in query.group_by_items],
        having=None if query.having is None else normalize_expression(query.having),
        order_by_items=[normalize_order_by_item(item) for item in query.order_by_items],
        limit=query.limit,
        offset=query.offset,
        distinct=query.distinct,
        set_operations=[normalize_set_operation(item) for item in query.set_operations],
    )


def normalize_compile_input(compile_input: SQLCompileInput) -> SQLCompileInput:
    """Return one normalized compiler input."""
    return SQLCompileInput(
        target_name=normalize_identifier(compile_input.target_name),
        source=None if compile_input.source is None else normalize_source(compile_input.source),
        joins=[normalize_join(join) for join in compile_input.joins],
        select_items=[normalize_select_item(item) for item in compile_input.select_items],
        where=None if compile_input.where is None else normalize_expression(compile_input.where),
        group_by_items=[normalize_expression(item) for item in compile_input.group_by_items],
        having=None if compile_input.having is None else normalize_expression(compile_input.having),
        order_by_items=[normalize_order_by_item(item) for item in compile_input.order_by_items],
        limit=compile_input.limit,
        offset=compile_input.offset,
        distinct=compile_input.distinct,
        set_operations=[normalize_set_operation(item) for item in compile_input.set_operations],
        ctes=[
            SQLCommonTableExpression(
                name=normalize_identifier(cte.name),
                query=normalize_query(cte.query),
            )
            for cte in compile_input.ctes
        ],
    )
