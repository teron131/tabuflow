"""Import free-form SQLite SQL into the deterministic compiler subset."""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from .types import (
    SQLCommonTableExpression,
    SQLCompileInput,
    SQLCompileIssue,
    SQLExpression,
    SQLJoin,
    SQLOrderByItem,
    SQLQuery,
    SQLSelectItem,
    SQLSetOperation,
    SQLSource,
)

SET_OPERATOR_BY_TYPE = {
    exp.Union: "union_all",
    exp.Intersect: "intersect",
    exp.Except: "except",
}

BINARY_OPERATOR_BY_TYPE = {
    exp.EQ: "=",
    exp.NEQ: "!=",
    exp.GT: ">",
    exp.GTE: ">=",
    exp.LT: "<",
    exp.LTE: "<=",
    exp.And: "AND",
    exp.Or: "OR",
    exp.Like: "LIKE",
    exp.Is: "IS",
    exp.Add: "+",
    exp.Sub: "-",
    exp.Mul: "*",
    exp.Div: "/",
}

UNARY_OPERATOR_BY_TYPE = {
    exp.Not: "NOT",
    exp.Neg: "-",
    exp.Paren: None,
    exp.Exists: "EXISTS",
}


@dataclass(slots=True)
class SQLImportError(Exception):
    """One parse/import error when converting SQL text into the compiler subset."""

    code: str
    field: str
    message: str
    value: str | None = None

    def to_issue(self) -> SQLCompileIssue:
        """Convert the import error into one compiler issue."""
        return SQLCompileIssue(
            code=self.code,  # type: ignore[arg-type]
            field=self.field,
            value=self.value,
            message=self.message,
        )


def import_compile_input(query_input: SQLCompileInput | SQLQuery | str) -> SQLCompileInput:
    """Return compiler-ready input from structured or raw SQL input."""
    if isinstance(query_input, SQLCompileInput):
        return query_input
    if isinstance(query_input, SQLQuery):
        return wrap_query_input(query_input)
    return parse_sql_text(query_input)


def wrap_query_input(query: SQLQuery) -> SQLCompileInput:
    """Wrap a plain SQLQuery in SQLCompileInput by inferring the authoritative target."""
    target_name = infer_target_name(query, ctes=[])
    return SQLCompileInput(
        target_name=target_name,
        source=query.source,
        joins=query.joins,
        select_items=query.select_items,
        where=query.where,
        group_by_items=query.group_by_items,
        having=query.having,
        order_by_items=query.order_by_items,
        limit=query.limit,
        offset=query.offset,
        distinct=query.distinct,
        set_operations=query.set_operations,
    )


def parse_sql_text(sql_text: str) -> SQLCompileInput:
    """Parse one raw SQL query string into the compiler subset."""
    try:
        statement = parse_one(sql_text, dialect="sqlite")
    except ParseError as exc:
        raise SQLImportError(
            code="invalid_sql_text",
            field="sql",
            value=sql_text,
            message=f"Could not parse SQL text: {exc}",
        ) from exc

    cte_names = extract_cte_names(statement)
    ctes = parse_common_table_expressions(statement, cte_names=cte_names)
    query = parse_query_expression(statement, cte_names=cte_names)
    target_name = infer_target_name(query, ctes=ctes)
    return SQLCompileInput(
        target_name=target_name,
        ctes=ctes,
        source=query.source,
        joins=query.joins,
        select_items=query.select_items,
        where=query.where,
        group_by_items=query.group_by_items,
        having=query.having,
        order_by_items=query.order_by_items,
        limit=query.limit,
        offset=query.offset,
        distinct=query.distinct,
        set_operations=query.set_operations,
    )


def extract_cte_names(statement: exp.Expression) -> set[str]:
    """Return all CTE names declared on the statement."""
    with_expression = statement.args.get("with_")
    if with_expression is None:
        return set()
    return {cte.alias_or_name for cte in with_expression.expressions}


def parse_common_table_expressions(
    statement: exp.Expression,
    *,
    cte_names: set[str],
) -> list[SQLCommonTableExpression]:
    """Parse CTE declarations attached to one SQL statement."""
    with_expression = statement.args.get("with_")
    if with_expression is None:
        return []

    ctes: list[SQLCommonTableExpression] = []
    for cte in with_expression.expressions:
        if not isinstance(cte, exp.CTE):
            raise unsupported_sql_text(cte, "Unsupported WITH clause entry.")
        ctes.append(
            SQLCommonTableExpression(
                name=cte.alias_or_name,
                query=parse_query_expression(cte.this, cte_names=cte_names),
            )
        )
    return ctes


def parse_query_expression(
    expression: exp.Expression,
    *,
    cte_names: set[str],
) -> SQLQuery:
    """Parse one SQL query expression into the deterministic query shape."""
    if isinstance(expression, exp.Select):
        return parse_select_query(expression, cte_names=cte_names)
    if isinstance(expression, (exp.Union, exp.Intersect, exp.Except)):
        return parse_compound_query(expression, cte_names=cte_names)
    if isinstance(expression, exp.Subquery):
        return parse_query_expression(expression.this, cte_names=cte_names)
    raise unsupported_sql_text(expression, f"Unsupported SQL statement type: {type(expression).__name__}")


def parse_select_query(
    select: exp.Select,
    *,
    cte_names: set[str],
) -> SQLQuery:
    """Parse one SELECT statement into the structured query shape."""
    source = None if select.args.get("from_") is None else parse_source(select.args["from_"].this, cte_names=cte_names)
    joins = [parse_join(join, cte_names=cte_names) for join in select.args.get("joins") or []]
    where_expression = select.args.get("where")
    having_expression = select.args.get("having")
    order_expression = select.args.get("order")
    group_expression = select.args.get("group")

    return SQLQuery(
        source=source,
        joins=joins,
        select_items=[parse_select_item(item, cte_names=cte_names) for item in select.expressions],
        where=None if where_expression is None else parse_expression(where_expression.this, cte_names=cte_names),
        group_by_items=[] if group_expression is None else [parse_expression(item, cte_names=cte_names) for item in group_expression.expressions],
        having=None if having_expression is None else parse_expression(having_expression.this, cte_names=cte_names),
        order_by_items=[] if order_expression is None else [parse_order_by_item(item, cte_names=cte_names) for item in order_expression.expressions],
        limit=parse_integer_literal(select.args.get("limit"), field="sql.limit"),
        offset=parse_integer_literal(select.args.get("offset"), field="sql.offset"),
        distinct=select.args.get("distinct") is not None,
    )


def parse_compound_query(
    expression: exp.Expression,
    *,
    cte_names: set[str],
) -> SQLQuery:
    """Parse one UNION/INTERSECT/EXCEPT query into the structured query shape."""
    if type(expression) not in SET_OPERATOR_BY_TYPE:
        raise unsupported_sql_text(expression, f"Unsupported compound query expression: {type(expression).__name__}")

    base_query = parse_query_expression(expression.this, cte_names=cte_names)
    branch_query = parse_query_expression(expression.expression, cte_names=cte_names)
    set_operations = [
        *base_query.set_operations,
        SQLSetOperation(
            operator=set_operator_for(expression),
            query=branch_query,
        ),
    ]
    order_expression = expression.args.get("order")

    return SQLQuery(
        source=base_query.source,
        joins=base_query.joins,
        select_items=base_query.select_items,
        where=base_query.where,
        group_by_items=base_query.group_by_items,
        having=base_query.having,
        order_by_items=[] if order_expression is None else [parse_order_by_item(item, cte_names=cte_names) for item in order_expression.expressions],
        limit=parse_integer_literal(expression.args.get("limit"), field="sql.limit"),
        offset=parse_integer_literal(expression.args.get("offset"), field="sql.offset"),
        distinct=base_query.distinct,
        set_operations=set_operations,
    )


def parse_source(
    expression: exp.Expression,
    *,
    cte_names: set[str],
) -> SQLSource:
    """Parse one FROM source expression."""
    if isinstance(expression, exp.Table):
        source_name = expression.name
        source_kind = "cte" if source_name in cte_names else "target"
        source_kwargs = {"alias": alias_or_none(expression.alias)}
        if source_kind == "cte":
            return SQLSource(kind="cte", cte_name=source_name, **source_kwargs)
        return SQLSource(kind="target", target_name=source_name, **source_kwargs)

    if isinstance(expression, exp.Subquery):
        return SQLSource(
            kind="subquery",
            query=parse_query_expression(expression.this, cte_names=cte_names),
            alias=alias_or_none(expression.alias),
        )

    raise unsupported_sql_text(expression, f"Unsupported FROM source: {type(expression).__name__}")


def parse_join(
    join: exp.Join,
    *,
    cte_names: set[str],
) -> SQLJoin:
    """Parse one join clause."""
    join_type = "inner"
    if join.args.get("kind") == "CROSS":
        join_type = "cross"
    elif join.args.get("side") == "LEFT":
        join_type = "left"

    on_expression = join.args.get("on")
    return SQLJoin(
        join_type=join_type,  # type: ignore[arg-type]
        source=parse_source(join.this, cte_names=cte_names),
        on=None if on_expression is None else parse_expression(on_expression, cte_names=cte_names),
    )


def parse_select_item(item: exp.Expression, *, cte_names: set[str]) -> SQLSelectItem:
    """Parse one projected SELECT item."""
    alias = None
    expression = item
    if isinstance(item, exp.Alias):
        alias = item.alias
        expression = item.this

    if isinstance(expression, exp.Column):
        return SQLSelectItem(
            column_name=expression.name,
            source_alias=identifier_name(expression.table),
            alias=alias,
        )

    parsed_expression = parse_expression(expression, cte_names=cte_names)
    if parsed_expression.kind == "star":
        raise unsupported_sql_text(item, "SELECT * is not supported by the deterministic SQL compiler.")
    return SQLSelectItem(expression=parsed_expression, alias=alias)


def parse_order_by_item(item: exp.Ordered, *, cte_names: set[str]) -> SQLOrderByItem:
    """Parse one ORDER BY item."""
    expression = item.this
    direction = "desc" if item.args.get("desc") else "asc"

    if isinstance(expression, exp.Column):
        return SQLOrderByItem(
            column_name=expression.name,
            source_alias=identifier_name(expression.table),
            direction=direction,
        )
    return SQLOrderByItem(
        expression=parse_expression(expression, cte_names=cte_names),
        direction=direction,
    )


def parse_expression(expression: exp.Expression, *, cte_names: set[str]) -> SQLExpression:
    """Parse one SQL expression into the compiler subset."""
    if isinstance(expression, exp.Paren):
        return parse_expression(expression.this, cte_names=cte_names)
    if isinstance(expression, exp.Column):
        return SQLExpression(
            kind="column",
            column_name=expression.name,
            source_alias=identifier_name(expression.table),
        )
    if isinstance(expression, exp.Star):
        return SQLExpression(kind="star")
    if isinstance(expression, exp.Null):
        return SQLExpression(kind="literal", value=None)
    if isinstance(expression, exp.Boolean):
        return SQLExpression(kind="literal", value=expression.this)
    if isinstance(expression, exp.Literal):
        return SQLExpression(kind="literal", value=literal_value(expression))
    if isinstance(expression, exp.Case):
        return parse_case_expression(expression, cte_names=cte_names)
    if isinstance(expression, exp.Subquery):
        return SQLExpression(
            kind="subquery",
            query=parse_query_expression(expression.this, cte_names=cte_names),
        )
    if isinstance(expression, exp.Func):
        return SQLExpression(
            kind="function",
            function_name=expression.sql_name().lower(),
            arguments=parse_function_arguments(expression, cte_names=cte_names),
        )
    if isinstance(expression, exp.Not):
        return parse_not_expression(expression, cte_names=cte_names)
    if isinstance(expression, exp.In):
        return SQLExpression(
            kind="binary_op",
            operator="IN",
            left=parse_expression(expression.this, cte_names=cte_names),
            right=parse_in_right_expression(expression, cte_names=cte_names),
        )
    if isinstance(expression, exp.Between):
        return SQLExpression(
            kind="binary_op",
            operator="BETWEEN",
            left=parse_expression(expression.this, cte_names=cte_names),
            right=SQLExpression(
                kind="list",
                items=[
                    parse_expression(expression.args["low"], cte_names=cte_names),
                    parse_expression(expression.args["high"], cte_names=cte_names),
                ],
            ),
        )

    operator = BINARY_OPERATOR_BY_TYPE.get(type(expression))
    if operator is not None:
        return SQLExpression(
            kind="binary_op",
            operator=operator,  # type: ignore[arg-type]
            left=parse_expression(expression.this, cte_names=cte_names),
            right=parse_expression(expression.expression, cte_names=cte_names),
        )

    unary_operator = UNARY_OPERATOR_BY_TYPE.get(type(expression))
    if unary_operator is not None:
        if unary_operator is None:
            return parse_expression(expression.this, cte_names=cte_names)
        operand_expression = expression.this
        if isinstance(expression, exp.Exists):
            operand_expression = exp.Subquery(this=expression.this)
        return SQLExpression(
            kind="unary_op",
            operator=unary_operator,  # type: ignore[arg-type]
            operand=parse_expression(operand_expression, cte_names=cte_names),
        )

    raise unsupported_sql_text(expression, f"Unsupported SQL expression: {type(expression).__name__}")


def parse_case_expression(expression: exp.Case, *, cte_names: set[str]) -> SQLExpression:
    """Parse one CASE expression."""
    conditions: list[SQLExpression] = []
    results: list[SQLExpression] = []
    for if_expression in expression.args.get("ifs") or []:
        conditions.append(parse_expression(if_expression.this, cte_names=cte_names))
        results.append(parse_expression(if_expression.args["true"], cte_names=cte_names))
    default_expression = expression.args.get("default")
    return SQLExpression(
        kind="case",
        conditions=conditions,
        results=results,
        else_expression=None if default_expression is None else parse_expression(default_expression, cte_names=cte_names),
    )


def parse_function_arguments(expression: exp.Func, *, cte_names: set[str]) -> list[SQLExpression]:
    """Parse function arguments from sqlglot's expression layout."""
    arguments: list[SQLExpression] = []
    for key in ("this", "expression"):
        argument = expression.args.get(key)
        if argument is not None:
            arguments.append(parse_expression(argument, cte_names=cte_names))
    arguments.extend(parse_expression(argument, cte_names=cte_names) for argument in expression.expressions)
    return arguments


def parse_not_expression(expression: exp.Not, *, cte_names: set[str]) -> SQLExpression:
    """Parse NOT and the normalized NOT LIKE/IN/BETWEEN/IS patterns."""
    inner = expression.this
    if isinstance(inner, exp.In):
        return SQLExpression(
            kind="binary_op",
            operator="NOT IN",
            left=parse_expression(inner.this, cte_names=cte_names),
            right=parse_in_right_expression(inner, cte_names=cte_names),
        )
    if isinstance(inner, exp.Between):
        return SQLExpression(
            kind="binary_op",
            operator="NOT BETWEEN",
            left=parse_expression(inner.this, cte_names=cte_names),
            right=SQLExpression(
                kind="list",
                items=[
                    parse_expression(inner.args["low"], cte_names=cte_names),
                    parse_expression(inner.args["high"], cte_names=cte_names),
                ],
            ),
        )
    if isinstance(inner, exp.Like):
        return SQLExpression(
            kind="binary_op",
            operator="NOT LIKE",
            left=parse_expression(inner.this, cte_names=cte_names),
            right=parse_expression(inner.expression, cte_names=cte_names),
        )
    if isinstance(inner, exp.Is):
        return SQLExpression(
            kind="binary_op",
            operator="IS NOT",
            left=parse_expression(inner.this, cte_names=cte_names),
            right=parse_expression(inner.expression, cte_names=cte_names),
        )
    return SQLExpression(
        kind="unary_op",
        operator="NOT",
        operand=parse_expression(inner, cte_names=cte_names),
    )


def parse_in_right_expression(expression: exp.In, *, cte_names: set[str]) -> SQLExpression:
    """Parse the right side of one IN expression."""
    query_expression = expression.args.get("query")
    if query_expression is not None:
        return parse_expression(query_expression, cte_names=cte_names)
    expressions = expression.args.get("expressions") or []
    return SQLExpression(
        kind="list",
        items=[parse_expression(item, cte_names=cte_names) for item in expressions],
    )


def parse_integer_literal(expression: exp.Expression | None, *, field: str) -> int | None:
    """Parse one LIMIT or OFFSET expression as an integer literal."""
    if expression is None:
        return None
    literal = expression.args.get("expression") if isinstance(expression, (exp.Limit, exp.Offset)) else expression
    if not isinstance(literal, exp.Literal) or literal.is_string:
        raise unsupported_sql_text(expression, f"Expected integer literal at {field}.")
    return int(literal.this)


def literal_value(expression: exp.Literal) -> str | int | float | bool | None:
    """Return one Python literal value from sqlglot literal expression."""
    if expression.is_string:
        return expression.this
    if "." in expression.this:
        return float(expression.this)
    return int(expression.this)


def set_operator_for(expression: exp.Expression) -> str:
    """Return the structured set operator for one sqlglot compound expression."""
    if isinstance(expression, exp.Union):
        return "union" if expression.args.get("distinct", True) else "union_all"
    if isinstance(expression, exp.Intersect):
        return "intersect"
    if isinstance(expression, exp.Except):
        return "except"
    raise unsupported_sql_text(expression, f"Unsupported set operator: {type(expression).__name__}")


def alias_or_none(alias: str) -> str | None:
    """Return the alias when present and non-empty."""
    return alias or None


def identifier_name(identifier: exp.Identifier | str | None) -> str | None:
    """Return one identifier name or None."""
    if identifier is None:
        return None
    if isinstance(identifier, exp.Identifier):
        return identifier.this
    return identifier


def infer_target_name(query: SQLQuery, *, ctes: list[SQLCommonTableExpression]) -> str:
    """Infer the authoritative target from the first base target reference."""
    target_name = first_target_name_in_query(query)
    if target_name is not None:
        return target_name
    for cte in ctes:
        target_name = first_target_name_in_query(cte.query)
        if target_name is not None:
            return target_name
    raise SQLImportError(
        code="invalid_sql_text",
        field="sql",
        message="Could not infer an authoritative target from the SQL text.",
    )


def first_target_name_in_query(query: SQLQuery) -> str | None:
    """Return the first explicit target reference inside one query tree."""
    if query.source is not None:
        target_name = first_target_name_in_source(query.source)
        if target_name is not None:
            return target_name
    for join in query.joins:
        target_name = first_target_name_in_source(join.source)
        if target_name is not None:
            return target_name
    for set_operation in query.set_operations:
        target_name = first_target_name_in_query(set_operation.query)
        if target_name is not None:
            return target_name
    if query.where is not None:
        target_name = first_target_name_in_expression(query.where)
        if target_name is not None:
            return target_name
    if query.having is not None:
        target_name = first_target_name_in_expression(query.having)
        if target_name is not None:
            return target_name
    for group_item in query.group_by_items:
        target_name = first_target_name_in_expression(group_item)
        if target_name is not None:
            return target_name
    for select_item in query.select_items:
        if select_item.expression is None:
            continue
        target_name = first_target_name_in_expression(select_item.expression)
        if target_name is not None:
            return target_name
    for order_by_item in query.order_by_items:
        if order_by_item.expression is None:
            continue
        target_name = first_target_name_in_expression(order_by_item.expression)
        if target_name is not None:
            return target_name
    return None


def first_target_name_in_source(source: SQLSource) -> str | None:
    """Return the first target name inside one source tree."""
    if source.kind == "target":
        return source.target_name
    if source.kind == "subquery" and source.query is not None:
        return first_target_name_in_query(source.query)
    return None


def first_target_name_in_expression(expression: SQLExpression) -> str | None:
    """Return the first target name nested inside one expression tree."""
    for nested_expression in expression.arguments:
        target_name = first_target_name_in_expression(nested_expression)
        if target_name is not None:
            return target_name
    for nested_expression in expression.items:
        target_name = first_target_name_in_expression(nested_expression)
        if target_name is not None:
            return target_name
    for nested_expression in expression.conditions:
        target_name = first_target_name_in_expression(nested_expression)
        if target_name is not None:
            return target_name
    for nested_expression in expression.results:
        target_name = first_target_name_in_expression(nested_expression)
        if target_name is not None:
            return target_name
    for nested_expression in (expression.left, expression.right, expression.operand, expression.else_expression):
        if nested_expression is None:
            continue
        target_name = first_target_name_in_expression(nested_expression)
        if target_name is not None:
            return target_name
    if expression.query is not None:
        return first_target_name_in_query(expression.query)
    return None


def unsupported_sql_text(expression: exp.Expression, message: str) -> SQLImportError:
    """Return one unsupported-SQL import error for the supplied expression."""
    return SQLImportError(
        code="unsupported_sql_text",
        field="sql",
        value=expression.sql(dialect="sqlite"),
        message=message,
    )
