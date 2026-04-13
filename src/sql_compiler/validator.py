"""Shared validation helpers for deterministic SQL compiler inputs."""

from __future__ import annotations

from collections.abc import Collection

from .types import (
    AGGREGATE_FUNCTION_NAMES,
    SAFE_IDENTIFIER_PATTERN,
    SUPPORTED_FUNCTION_NAMES,
    QueryScope,
    SQLCompileIssue,
    SQLCompileIssueCode,
    SQLExpression,
)


def is_safe_identifier(identifier: str) -> bool:
    """Return whether one identifier is safe for direct SQLite rendering."""
    return bool(identifier) and SAFE_IDENTIFIER_PATTERN.fullmatch(identifier) is not None


def append_issue(
    issues: list[SQLCompileIssue],
    *,
    code: SQLCompileIssueCode,
    field: str,
    value: str | None,
    message: str,
) -> None:
    """Append one deterministic compiler issue."""
    issues.append(SQLCompileIssue(code=code, field=field, value=value, message=message))


def append_invalid_expression(
    issues: list[SQLCompileIssue],
    *,
    field: str,
    message: str,
    value: str | None = None,
) -> None:
    """Append one invalid-expression issue with a consistent shape."""
    append_issue(
        issues,
        code="invalid_expression",
        field=field,
        value=value,
        message=message,
    )


def expression_or_null(expression: SQLExpression | None) -> SQLExpression:
    """Return the expression or a neutral NULL literal placeholder."""
    return expression if expression is not None else SQLExpression(kind="literal", value=None)


def scope_from_source(available_names: set[str] | None, qualifier_name: str | None) -> QueryScope:
    """Build one query scope from one resolved source."""
    qualified_names: dict[str, set[str] | None] = {}
    if qualifier_name is not None:
        qualified_names[qualifier_name] = None if available_names is None else set(available_names)
    return QueryScope(
        unqualified_names=None if available_names is None else set(available_names),
        ambiguous_names=set(),
        qualified_names=qualified_names,
    )


def merge_scopes(left: QueryScope, right: QueryScope) -> QueryScope:
    """Merge two query scopes."""
    if left.unqualified_names is None or right.unqualified_names is None:
        unqualified_names = None
        ambiguous_names = set(left.ambiguous_names) | set(right.ambiguous_names)
    else:
        left_names = set(left.unqualified_names)
        right_names = set(right.unqualified_names)
        unqualified_names = left_names | right_names
        ambiguous_names = set(left.ambiguous_names) | set(right.ambiguous_names) | (left_names & right_names)

    qualified_names = dict(left.qualified_names)
    qualified_names.update(right.qualified_names)
    return QueryScope(
        unqualified_names=unqualified_names,
        ambiguous_names=ambiguous_names,
        qualified_names=qualified_names,
    )


def extend_scope_with_output_names(scope: QueryScope, output_names: Collection[str]) -> QueryScope:
    """Add projected output names as unqualified references."""
    unqualified_names = None if scope.unqualified_names is None else set(scope.unqualified_names) | {name for name in output_names if name}
    return QueryScope(
        unqualified_names=unqualified_names,
        ambiguous_names=set(scope.ambiguous_names),
        qualified_names=dict(scope.qualified_names),
    )


def normalized_function_name(function_name: str | None) -> str:
    """Normalize one SQL function name for policy checks."""
    return (function_name or "").strip().lower()


def is_aggregate_function_name(function_name: str | None) -> bool:
    """Return whether one function name is an allowed aggregate."""
    return normalized_function_name(function_name) in AGGREGATE_FUNCTION_NAMES


def is_supported_function_name(function_name: str | None) -> bool:
    """Return whether one function name is in the deterministic allowlist."""
    return normalized_function_name(function_name) in SUPPORTED_FUNCTION_NAMES


def validate_column_reference(
    *,
    column_name: str,
    source_alias: str | None,
    field_prefix: str,
    issues: list[SQLCompileIssue],
    unsafe_code: SQLCompileIssueCode,
    missing_code: SQLCompileIssueCode,
    scope: QueryScope,
) -> None:
    """Validate one plain or qualified column reference."""
    if not is_safe_identifier(column_name):
        append_issue(
            issues,
            code=unsafe_code,
            field=f"{field_prefix}.column_name",
            value=column_name or None,
            message=f"Unsafe SQLite column identifier: {column_name}",
        )
        return

    if source_alias is not None:
        if not is_safe_identifier(source_alias):
            append_issue(
                issues,
                code="unsafe_source_qualifier",
                field=f"{field_prefix}.source_alias",
                value=source_alias,
                message=f"Unsafe SQLite source qualifier identifier: {source_alias}",
            )
            return
        available_names = scope.qualified_names.get(source_alias)
        if source_alias not in scope.qualified_names:
            append_issue(
                issues,
                code="unknown_source_alias",
                field=f"{field_prefix}.source_alias",
                value=source_alias,
                message=f"Unknown source alias or qualifier: {source_alias}",
            )
            return
        if available_names is not None and column_name not in available_names:
            append_issue(
                issues,
                code=missing_code,
                field=f"{field_prefix}.column_name",
                value=f"{source_alias}.{column_name}",
                message=f"Column is not present in the available source outputs: {source_alias}.{column_name}",
            )
            return
        return

    if scope.unqualified_names is not None and column_name not in scope.unqualified_names:
        append_issue(
            issues,
            code=missing_code,
            field=f"{field_prefix}.column_name",
            value=column_name,
            message=f"Column is not present in the available source outputs: {column_name}",
        )
        return

    if column_name in scope.ambiguous_names:
        append_issue(
            issues,
            code="ambiguous_column",
            field=f"{field_prefix}.column_name",
            value=column_name,
            message=f"Unqualified column reference is ambiguous after joining sources: {column_name}",
        )
