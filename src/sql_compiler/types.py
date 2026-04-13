"""Types and constants for the deterministic SQL compiler."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from pydantic import BaseModel, Field

SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

SQLOrderDirection = Literal["asc", "desc"]
SQLCompileStatus = Literal["ok", "error"]
SQLExpressionKind = Literal["column", "literal", "function", "binary_op", "unary_op", "subquery", "list", "case", "star"]
SQLSourceKind = Literal["target", "cte", "subquery"]
SQLJoinType = Literal["inner", "left", "cross"]
SQLSetOperator = Literal["union", "union_all", "intersect", "except"]
SQLBinaryOperator = Literal["=", "!=", "<>", "<", "<=", ">", ">=", "AND", "OR", "LIKE", "NOT LIKE", "IN", "NOT IN", "IS", "IS NOT", "BETWEEN", "NOT BETWEEN", "+", "-", "*", "/"]
SQLUnaryOperator = Literal["NOT", "+", "-", "EXISTS"]
SQLCompileIssueCode = Literal[
    "empty_select",
    "ambiguous_column",
    "unsafe_target",
    "unsafe_source_name",
    "unsafe_source_alias",
    "unsafe_source_qualifier",
    "unsafe_select_column",
    "unsafe_select_alias",
    "unsafe_order_by_column",
    "unsafe_expression_column",
    "unsafe_function_name",
    "unsupported_function",
    "unsafe_cte_name",
    "missing_select_column",
    "missing_order_by_column",
    "missing_expression_column",
    "duplicate_select_output",
    "duplicate_order_by_column",
    "duplicate_cte_name",
    "unknown_cte_name",
    "unknown_source_alias",
    "target_mismatch",
    "invalid_expression",
    "invalid_source",
    "invalid_join",
    "invalid_set_operation",
    "invalid_aggregation",
    "invalid_limit",
    "invalid_offset",
    "invalid_sql_text",
    "unsupported_sql_text",
]

AGGREGATE_FUNCTION_NAMES = frozenset({"avg", "count", "max", "min", "sum", "total"})
SUPPORTED_FUNCTION_NAMES = frozenset(
    {
        "abs",
        "avg",
        "coalesce",
        "count",
        "ifnull",
        "instr",
        "length",
        "lower",
        "ltrim",
        "max",
        "min",
        "nullif",
        "replace",
        "round",
        "rtrim",
        "substr",
        "sum",
        "total",
        "trim",
        "typeof",
        "upper",
    }
)


class SQLExpression(BaseModel):
    """One structured SQL expression."""

    kind: SQLExpressionKind
    column_name: str | None = None
    source_alias: str | None = None
    value: str | int | float | bool | None = None
    function_name: str | None = None
    arguments: list[SQLExpression] = Field(default_factory=list)
    operator: SQLBinaryOperator | SQLUnaryOperator | None = None
    left: SQLExpression | None = None
    right: SQLExpression | None = None
    operand: SQLExpression | None = None
    query: SQLQuery | None = None
    items: list[SQLExpression] = Field(default_factory=list)
    conditions: list[SQLExpression] = Field(default_factory=list)
    results: list[SQLExpression] = Field(default_factory=list)
    else_expression: SQLExpression | None = None


class SQLSource(BaseModel):
    """One structured SQL FROM source."""

    kind: SQLSourceKind = "target"
    target_name: str | None = None
    cte_name: str | None = None
    query: SQLQuery | None = None
    alias: str | None = None


class SQLJoin(BaseModel):
    """One deterministic join edge."""

    join_type: SQLJoinType = "inner"
    source: SQLSource
    on: SQLExpression | None = None


class SQLSelectItem(BaseModel):
    """One deterministic select item."""

    column_name: str | None = None
    source_alias: str | None = None
    expression: SQLExpression | None = None
    alias: str | None = None


class SQLOrderByItem(BaseModel):
    """One deterministic order-by item."""

    column_name: str | None = None
    source_alias: str | None = None
    expression: SQLExpression | None = None
    direction: SQLOrderDirection = "asc"


class SQLSetOperation(BaseModel):
    """One deterministic compound-query branch."""

    operator: SQLSetOperator = "union"
    query: SQLQuery


class SQLQuery(BaseModel):
    """One structured SQL query block."""

    source: SQLSource | None = None
    joins: list[SQLJoin] = Field(default_factory=list)
    select_items: list[SQLSelectItem] = Field(default_factory=list)
    where: SQLExpression | None = None
    group_by_items: list[SQLExpression] = Field(default_factory=list)
    having: SQLExpression | None = None
    order_by_items: list[SQLOrderByItem] = Field(default_factory=list)
    limit: int | None = None
    offset: int | None = None
    distinct: bool = False
    set_operations: list[SQLSetOperation] = Field(default_factory=list)


class SQLCommonTableExpression(BaseModel):
    """One deterministic SQL common table expression."""

    name: str
    query: SQLQuery


class SQLCompileInput(SQLQuery):
    """Compiler-ready SQL query shape with authoritative target constraints."""

    target_name: str
    ctes: list[SQLCommonTableExpression] = Field(default_factory=list)


class SQLCompileIssue(BaseModel):
    """One compiler validation issue."""

    code: SQLCompileIssueCode
    field: str
    value: str | None = None
    message: str


class SQLCompileResult(BaseModel):
    """Compiler output with normalized input and deterministic diagnostics."""

    status: SQLCompileStatus
    normalized: SQLCompileInput
    sql: str | None = None
    issues: list[SQLCompileIssue] = Field(default_factory=list)


@dataclass(slots=True)
class CompiledQuery:
    """Internal compiled SQL fragment plus projected output names."""

    sql: str
    output_names: list[str]
    selectable_names: set[str]
    projected_column_count: int


@dataclass(slots=True)
class CompileContext:
    """Internal recursive compiler context."""

    authoritative_target_name: str
    authoritative_columns: set[str] | None
    cte_outputs: dict[str, set[str]]


@dataclass(slots=True)
class ResolvedSource:
    """One resolved SQL source with optional scope metadata."""

    sql: str
    available_names: set[str] | None
    qualifier_name: str | None


@dataclass(slots=True)
class QueryScope:
    """Available names for one query block."""

    unqualified_names: set[str] | None
    ambiguous_names: set[str]
    qualified_names: dict[str, set[str] | None]


SQLExpression.model_rebuild()
SQLSource.model_rebuild()
SQLJoin.model_rebuild()
SQLSetOperation.model_rebuild()
SQLQuery.model_rebuild()
