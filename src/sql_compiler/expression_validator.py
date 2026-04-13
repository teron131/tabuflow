"""Expression validation for deterministic SQL compiler inputs."""

from __future__ import annotations

from collections.abc import Callable

from .types import CompileContext, CompiledQuery, QueryScope, SQLCompileIssue, SQLExpression, SQLQuery
from .validator import append_invalid_expression, append_issue, is_safe_identifier, is_supported_function_name, validate_column_reference

CompileQueryFn = Callable[[SQLQuery, list[SQLCompileIssue], str, CompileContext], CompiledQuery]
RenderExpressionFn = Callable[..., str]


def validate_function_expression(
    expression: SQLExpression,
    *,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    scope: QueryScope,
    context: CompileContext,
    compile_query_fn: CompileQueryFn,
    render_expression_fn: RenderExpressionFn,
) -> None:
    """Validate one function-call expression."""
    function_name = expression.function_name or ""
    if not is_safe_identifier(function_name):
        append_issue(
            issues,
            code="unsafe_function_name",
            field=f"{field_prefix}.function_name",
            value=function_name or None,
            message=f"Unsafe SQLite function identifier: {function_name}",
        )
    elif not is_supported_function_name(function_name):
        append_issue(
            issues,
            code="unsupported_function",
            field=f"{field_prefix}.function_name",
            value=function_name,
            message=f"Unsupported SQL function for deterministic compilation: {function_name}",
        )

    if not expression.arguments:
        append_invalid_expression(
            issues,
            field=f"{field_prefix}.arguments",
            message="Function expressions must contain at least one argument.",
        )

    for argument_idx, argument in enumerate(expression.arguments):
        validate_expression(
            argument,
            field_prefix=f"{field_prefix}.arguments[{argument_idx}]",
            issues=issues,
            scope=scope,
            context=context,
            compile_query_fn=compile_query_fn,
            render_expression_fn=render_expression_fn,
        )


def validate_binary_expression(
    expression: SQLExpression,
    *,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    scope: QueryScope,
    context: CompileContext,
    compile_query_fn: CompileQueryFn,
    render_expression_fn: RenderExpressionFn,
) -> None:
    """Validate one binary expression."""
    if expression.left is None or expression.right is None or expression.operator is None:
        append_invalid_expression(
            issues,
            field=field_prefix,
            message="Binary expressions require left, right, and operator fields.",
        )
        return

    validate_expression(
        expression.left,
        field_prefix=f"{field_prefix}.left",
        issues=issues,
        scope=scope,
        context=context,
        compile_query_fn=compile_query_fn,
        render_expression_fn=render_expression_fn,
    )
    validate_expression(
        expression.right,
        field_prefix=f"{field_prefix}.right",
        issues=issues,
        scope=scope,
        context=context,
        compile_query_fn=compile_query_fn,
        render_expression_fn=render_expression_fn,
    )
    if expression.operator in {"BETWEEN", "NOT BETWEEN"} and (expression.right.kind != "list" or len(expression.right.items) != 2):
        append_invalid_expression(
            issues,
            field=f"{field_prefix}.right",
            value=expression.operator,
            message="BETWEEN expressions require the right side to be a two-item list expression.",
        )


def validate_unary_expression(
    expression: SQLExpression,
    *,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    scope: QueryScope,
    context: CompileContext,
    compile_query_fn: CompileQueryFn,
    render_expression_fn: RenderExpressionFn,
) -> None:
    """Validate one unary expression."""
    if expression.operand is None or expression.operator is None:
        append_invalid_expression(
            issues,
            field=field_prefix,
            message="Unary expressions require operand and operator fields.",
        )
        return

    validate_expression(
        expression.operand,
        field_prefix=f"{field_prefix}.operand",
        issues=issues,
        scope=scope,
        context=context,
        compile_query_fn=compile_query_fn,
        render_expression_fn=render_expression_fn,
    )
    if expression.operator == "EXISTS" and expression.operand.kind != "subquery":
        append_invalid_expression(
            issues,
            field=f"{field_prefix}.operand",
            value=expression.operator,
            message="EXISTS expressions require a subquery operand.",
        )


def validate_list_expression(
    expression: SQLExpression,
    *,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    scope: QueryScope,
    context: CompileContext,
    compile_query_fn: CompileQueryFn,
    render_expression_fn: RenderExpressionFn,
) -> None:
    """Validate one list expression."""
    if not expression.items:
        append_invalid_expression(
            issues,
            field=f"{field_prefix}.items",
            message="List expressions require at least one item.",
        )
        return

    for item_idx, item in enumerate(expression.items):
        validate_expression(
            item,
            field_prefix=f"{field_prefix}.items[{item_idx}]",
            issues=issues,
            scope=scope,
            context=context,
            compile_query_fn=compile_query_fn,
            render_expression_fn=render_expression_fn,
        )


def validate_subquery_expression(
    expression: SQLExpression,
    *,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    context: CompileContext,
    compile_query_fn: CompileQueryFn,
) -> None:
    """Validate one subquery expression."""
    if expression.query is None:
        append_invalid_expression(
            issues,
            field=f"{field_prefix}.query",
            message="Subquery expressions require a nested query.",
        )
        return

    compile_query_fn(
        expression.query,
        issues=issues,
        field_prefix=f"{field_prefix}.query",
        context=context,
    )


def validate_case_expression(
    expression: SQLExpression,
    *,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    scope: QueryScope,
    context: CompileContext,
    compile_query_fn: CompileQueryFn,
    render_expression_fn: RenderExpressionFn,
) -> None:
    """Validate one CASE expression."""
    if not expression.conditions or len(expression.conditions) != len(expression.results):
        append_invalid_expression(
            issues,
            field=field_prefix,
            message="CASE expressions require matching non-empty conditions and results.",
        )
        return

    for case_idx, condition in enumerate(expression.conditions):
        validate_expression(
            condition,
            field_prefix=f"{field_prefix}.conditions[{case_idx}]",
            issues=issues,
            scope=scope,
            context=context,
            compile_query_fn=compile_query_fn,
            render_expression_fn=render_expression_fn,
        )
        validate_expression(
            expression.results[case_idx],
            field_prefix=f"{field_prefix}.results[{case_idx}]",
            issues=issues,
            scope=scope,
            context=context,
            compile_query_fn=compile_query_fn,
            render_expression_fn=render_expression_fn,
        )

    if expression.else_expression is not None:
        validate_expression(
            expression.else_expression,
            field_prefix=f"{field_prefix}.else_expression",
            issues=issues,
            scope=scope,
            context=context,
            compile_query_fn=compile_query_fn,
            render_expression_fn=render_expression_fn,
        )


def validate_expression(
    expression: SQLExpression,
    *,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    scope: QueryScope,
    context: CompileContext,
    compile_query_fn: CompileQueryFn,
    render_expression_fn: RenderExpressionFn,
) -> None:
    """Validate one structured SQL expression recursively."""
    if expression.kind == "column":
        validate_column_reference(
            column_name=expression.column_name or "",
            source_alias=expression.source_alias,
            field_prefix=field_prefix,
            issues=issues,
            unsafe_code="unsafe_expression_column",
            missing_code="missing_expression_column",
            scope=scope,
        )
        return

    if expression.kind == "literal":
        return

    if expression.kind == "star":
        return

    if expression.kind == "function":
        validate_function_expression(
            expression,
            field_prefix=field_prefix,
            issues=issues,
            scope=scope,
            context=context,
            compile_query_fn=compile_query_fn,
            render_expression_fn=render_expression_fn,
        )
        return

    if expression.kind == "binary_op":
        validate_binary_expression(
            expression,
            field_prefix=field_prefix,
            issues=issues,
            scope=scope,
            context=context,
            compile_query_fn=compile_query_fn,
            render_expression_fn=render_expression_fn,
        )
        return

    if expression.kind == "unary_op":
        validate_unary_expression(
            expression,
            field_prefix=field_prefix,
            issues=issues,
            scope=scope,
            context=context,
            compile_query_fn=compile_query_fn,
            render_expression_fn=render_expression_fn,
        )
        return

    if expression.kind == "list":
        validate_list_expression(
            expression,
            field_prefix=field_prefix,
            issues=issues,
            scope=scope,
            context=context,
            compile_query_fn=compile_query_fn,
            render_expression_fn=render_expression_fn,
        )
        return

    if expression.kind == "subquery":
        validate_subquery_expression(
            expression,
            field_prefix=field_prefix,
            issues=issues,
            context=context,
            compile_query_fn=compile_query_fn,
        )
        return

    if expression.kind == "case":
        validate_case_expression(
            expression,
            field_prefix=field_prefix,
            issues=issues,
            scope=scope,
            context=context,
            compile_query_fn=compile_query_fn,
            render_expression_fn=render_expression_fn,
        )
        return

    append_invalid_expression(
        issues,
        field=field_prefix,
        value=expression.kind,
        message=f"Unsupported expression kind: {expression.kind}",
    )
