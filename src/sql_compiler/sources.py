"""Source resolution for deterministic SQL compiler queries."""

from __future__ import annotations

from collections.abc import Callable

from .renderer import indent_sql, render_source_sql
from .types import (
    CompileContext,
    CompiledQuery,
    QueryScope,
    ResolvedSource,
    SQLCompileIssue,
    SQLJoinType,
    SQLQuery,
    SQLSource,
)
from .validator import append_issue, is_safe_identifier, merge_scopes, scope_from_source

CompileQueryFn = Callable[[SQLQuery, list[SQLCompileIssue], str, CompileContext], CompiledQuery]
RenderExpressionFn = Callable[..., str]


def authoritative_source(context: CompileContext) -> ResolvedSource:
    """Return the authoritative target as one resolved source."""
    return ResolvedSource(
        sql=context.authoritative_target_name,
        available_names=context.authoritative_columns,
        qualifier_name=context.authoritative_target_name,
    )


def resolve_target_source(
    source: SQLSource,
    *,
    issues: list[SQLCompileIssue],
    field_prefix: str,
    context: CompileContext,
) -> ResolvedSource:
    """Resolve a target source against the authoritative target."""
    target_name = source.target_name or ""
    if not is_safe_identifier(target_name):
        append_issue(
            issues,
            code="unsafe_source_name",
            field=f"{field_prefix}.target_name",
            value=target_name or None,
            message=f"Unsafe SQLite source identifier: {target_name}",
        )
    elif target_name != context.authoritative_target_name:
        append_issue(
            issues,
            code="target_mismatch",
            field=f"{field_prefix}.target_name",
            value=target_name,
            message=f"Nested query attempted to switch authoritative target from {context.authoritative_target_name} to {target_name}.",
        )
    return ResolvedSource(
        sql=render_source_sql(target_name, alias=source.alias),
        available_names=context.authoritative_columns,
        qualifier_name=source.alias or target_name,
    )


def resolve_cte_source(
    source: SQLSource,
    *,
    issues: list[SQLCompileIssue],
    field_prefix: str,
    context: CompileContext,
) -> ResolvedSource:
    """Resolve a CTE source against known CTE outputs."""
    cte_name = source.cte_name or ""
    if not is_safe_identifier(cte_name):
        append_issue(
            issues,
            code="unsafe_source_name",
            field=f"{field_prefix}.cte_name",
            value=cte_name or None,
            message=f"Unsafe SQLite CTE source identifier: {cte_name}",
        )
    elif cte_name not in context.cte_outputs:
        append_issue(
            issues,
            code="unknown_cte_name",
            field=f"{field_prefix}.cte_name",
            value=cte_name,
            message=f"Query referenced an unknown CTE source: {cte_name}",
        )
    return ResolvedSource(
        sql=render_source_sql(cte_name, alias=source.alias),
        available_names=context.cte_outputs.get(cte_name),
        qualifier_name=source.alias or cte_name,
    )


def resolve_subquery_source(
    source: SQLSource,
    *,
    issues: list[SQLCompileIssue],
    field_prefix: str,
    context: CompileContext,
    compile_query_fn: CompileQueryFn,
) -> ResolvedSource:
    """Resolve a subquery source and expose its projected columns."""
    if source.query is None:
        append_issue(
            issues,
            code="invalid_source",
            field=f"{field_prefix}.query",
            value=None,
            message="Subquery source requires a nested query.",
        )
        return ResolvedSource(sql="(SELECT 1)", available_names=None, qualifier_name=source.alias)

    compiled_subquery = compile_query_fn(
        source.query,
        issues=issues,
        field_prefix=f"{field_prefix}.query",
        context=context,
    )
    return ResolvedSource(
        sql=render_source_sql(
            f"(\n{indent_sql(compiled_subquery.sql)}\n)",
            alias=source.alias,
        ),
        available_names=compiled_subquery.selectable_names,
        qualifier_name=source.alias,
    )


def resolve_explicit_source(
    source: SQLSource,
    *,
    issues: list[SQLCompileIssue],
    field_prefix: str,
    context: CompileContext,
    compile_query_fn: CompileQueryFn,
) -> ResolvedSource:
    """Resolve one explicit source node."""
    if source.alias is not None and not is_safe_identifier(source.alias):
        append_issue(
            issues,
            code="unsafe_source_alias",
            field=f"{field_prefix}.alias",
            value=source.alias,
            message=f"Unsafe SQLite source alias identifier: {source.alias}",
        )

    if source.kind == "target":
        return resolve_target_source(
            source,
            issues=issues,
            field_prefix=field_prefix,
            context=context,
        )
    if source.kind == "cte":
        return resolve_cte_source(
            source,
            issues=issues,
            field_prefix=field_prefix,
            context=context,
        )
    if source.kind == "subquery":
        return resolve_subquery_source(
            source,
            issues=issues,
            field_prefix=field_prefix,
            context=context,
            compile_query_fn=compile_query_fn,
        )

    append_issue(
        issues,
        code="invalid_source",
        field=f"{field_prefix}.kind",
        value=source.kind,
        message=f"Unsupported query source kind: {source.kind}",
    )
    return authoritative_source(context)


def resolve_base_source(
    query: SQLQuery,
    *,
    issues: list[SQLCompileIssue],
    field_prefix: str,
    context: CompileContext,
    compile_query_fn: CompileQueryFn,
) -> ResolvedSource:
    """Resolve the base FROM source for one query."""
    if query.source is None:
        return authoritative_source(context)
    return resolve_explicit_source(
        query.source,
        issues=issues,
        field_prefix=f"{field_prefix}.source",
        context=context,
        compile_query_fn=compile_query_fn,
    )


def join_keyword(join_type: SQLJoinType) -> str:
    """Render one SQL join keyword."""
    if join_type == "left":
        return "LEFT JOIN"
    if join_type == "cross":
        return "CROSS JOIN"
    return "JOIN"


def resolve_from_clause(
    query: SQLQuery,
    *,
    issues: list[SQLCompileIssue],
    field_prefix: str,
    context: CompileContext,
    compile_query_fn: CompileQueryFn,
    validate_expression_fn: Callable[..., None],
    render_expression_fn: RenderExpressionFn,
) -> tuple[str, QueryScope]:
    """Resolve the full FROM clause including safe joins."""
    base_source = resolve_base_source(
        query,
        issues=issues,
        field_prefix=field_prefix,
        context=context,
        compile_query_fn=compile_query_fn,
    )
    from_sql = base_source.sql
    scope = scope_from_source(base_source.available_names, base_source.qualifier_name)

    for join_idx, join in enumerate(query.joins):
        join_prefix = f"{field_prefix}.joins[{join_idx}]"
        resolved_join = resolve_explicit_source(
            join.source,
            issues=issues,
            field_prefix=f"{join_prefix}.source",
            context=context,
            compile_query_fn=compile_query_fn,
        )
        join_scope = scope_from_source(resolved_join.available_names, resolved_join.qualifier_name)

        duplicate_qualifiers = set(scope.qualified_names) & set(join_scope.qualified_names)
        if duplicate_qualifiers:
            qualifier_name = sorted(duplicate_qualifiers)[0]
            append_issue(
                issues,
                code="invalid_join",
                field=f"{join_prefix}.source.alias",
                value=qualifier_name,
                message=f"Joined source qualifier collides with an existing source qualifier: {qualifier_name}. Use distinct aliases for joined sources.",
            )

        join_validation_scope = merge_scopes(scope, join_scope)
        if join.join_type == "cross":
            if join.on is not None:
                append_issue(
                    issues,
                    code="invalid_join",
                    field=f"{join_prefix}.on",
                    value=None,
                    message="CROSS JOIN must not include an ON expression.",
                )
        elif join.on is None:
            append_issue(
                issues,
                code="invalid_join",
                field=f"{join_prefix}.on",
                value=None,
                message="INNER and LEFT joins require an ON expression.",
            )
        else:
            validate_expression_fn(
                join.on,
                field_prefix=f"{join_prefix}.on",
                issues=issues,
                scope=join_validation_scope,
                context=context,
                compile_query_fn=compile_query_fn,
                render_expression_fn=render_expression_fn,
            )

        join_sql = f"{join_keyword(join.join_type)} {resolved_join.sql}"
        if join.join_type != "cross" and join.on is not None:
            join_sql = f"{join_sql} ON {render_expression_fn(join.on, context=context)}"
        from_sql = f"{from_sql}\n{join_sql}"
        scope = join_validation_scope

    return from_sql, scope
