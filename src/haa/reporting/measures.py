"""Evaluate one indicator `measure` block against a data frame."""

from __future__ import annotations

import ast
import io
import re
import tokenize

import pandas as pd

MISSING = "(missing)"
PII_NEXT_STEP = "pick another column, or rename it in the clean copy if it is not personal data"

_FILTER_HINT = (
    "use comparisons such as \"head_sex == 'female'\", and/or, in [...], .isna(), "
    ".isin([...]) or .str.contains(...)"
)
# A backtick-quoted column name (pandas syntax) standing alone as a token. Quotes,
# backslashes, `#` and line breaks are not accepted inside it, so swapping it for a
# placeholder cannot move a string or comment boundary: the validated expression has
# the same structure as the one pandas parses.
_BACKTICK_NAME = re.compile(r"(?<![\w`])`([^`'\"\\#\r\n]+)`(?![\w`])")
_BOOL_OPS = (ast.And, ast.Or)
_UNARY_OPS = (ast.Not, ast.Invert, ast.USub, ast.UAdd)
_BIN_OPS = (ast.BitAnd, ast.BitOr, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)
_COMPARE_OPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn)
_CONSTANT_TYPES = (str, int, float, bool, type(None))
_STR_METHODS = {"contains", "startswith", "endswith"}
_CALL_KEYWORDS = {"case", "na", "regex"}


class NotComputable(Exception):
    """An indicator, or one breakdown category, cannot be computed; carries the reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def category_keys(series: pd.Series) -> pd.Series:
    """Stringify category values; missing values become MISSING."""
    return series.astype(str).mask(series.isna(), MISSING)


def ordered_categories(keys: pd.Series) -> list[str]:
    """Categories sorted by name, with MISSING last."""
    present = set(keys.unique())
    ordered = sorted(c for c in present if c != MISSING)
    if MISSING in present:
        ordered.append(MISSING)
    return ordered


def check_column(df: pd.DataFrame, column: object, pii: list[str]) -> str:
    if not isinstance(column, str) or not column:
        raise NotComputable("the measure does not name a column")
    if column in pii:
        raise NotComputable(
            f"column {column!r} looks like personal data, so reports cannot use it — "
            f"{PII_NEXT_STEP}"
        )
    if column not in df.columns:
        raise NotComputable(
            f"column {column!r} not found in the dataset — check profile_dataset"
        )
    return column


def _pii_in_filter(text: str, column: object) -> NotComputable:
    return NotComputable(
        f"filter {text!r} uses column {column!r}, which looks like personal data, so reports "
        f"cannot use it — {PII_NEXT_STEP}"
    )


class _Refused(Exception):
    """The filter uses syntax outside the allowlist."""


def _is_constant(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        node = node.operand
        return isinstance(node, ast.Constant) and type(node.value) in (int, float)
    return isinstance(node, ast.Constant) and type(node.value) in _CONSTANT_TYPES


def _is_constant_list(node: ast.AST) -> bool:
    return isinstance(node, (ast.List, ast.Tuple)) and all(_is_constant(e) for e in node.elts)


def _check_call(node: ast.Call, names: list[str]) -> None:
    """Only <column>.str.contains/startswith/endswith(...), .isna(), .notna(), .isin([...])."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        raise _Refused
    if (
        func.attr in _STR_METHODS
        and isinstance(func.value, ast.Attribute)
        and func.value.attr == "str"
    ):
        column = func.value.value
        args_ok = all(_is_constant(arg) for arg in node.args)
    elif func.attr in ("isna", "notna"):
        column, args_ok = func.value, not node.args
    elif func.attr == "isin":
        column = func.value
        args_ok = len(node.args) == 1 and _is_constant_list(node.args[0])
    else:
        raise _Refused
    keywords_ok = all(
        kw.arg in _CALL_KEYWORDS and _is_constant(kw.value) for kw in node.keywords
    )
    if not (isinstance(column, ast.Name) and args_ok and keywords_ok):
        raise _Refused
    names.append(column.id)


def _check_node(node: ast.AST, names: list[str]) -> None:
    """Raise _Refused unless every node is on the allowlist; collect the names used."""
    if isinstance(node, ast.Expression):
        _check_node(node.body, names)
    elif isinstance(node, ast.BoolOp) and isinstance(node.op, _BOOL_OPS):
        for value in node.values:
            _check_node(value, names)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, _UNARY_OPS):
        _check_node(node.operand, names)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, _BIN_OPS):
        _check_node(node.left, names)
        _check_node(node.right, names)
    elif isinstance(node, ast.Compare) and all(isinstance(op, _COMPARE_OPS) for op in node.ops):
        for operand in (node.left, *node.comparators):
            _check_node(operand, names)
    elif isinstance(node, ast.Name):
        names.append(node.id)
    elif isinstance(node, ast.Call):
        _check_call(node, names)
    elif not (_is_constant(node) or _is_constant_list(node)):
        raise _Refused


def _is_row_condition(node: ast.AST) -> bool:
    """True when the expression yields a per-row True/False mask.

    A bare column, a constant or arithmetic is refused: pandas would try to use its
    values as row labels and put them in the error text.
    """
    if isinstance(node, (ast.Compare, ast.Call)):  # calls are limited by _check_call
        return True
    if isinstance(node, ast.BoolOp):
        return all(_is_row_condition(value) for value in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.Invert)):
        return _is_row_condition(node.operand)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitAnd, ast.BitOr)):
        return _is_row_condition(node.left) and _is_row_condition(node.right)
    return False


def _pandas_booleans(source: str) -> str:
    """Rewrite `&`/`|` as `and`/`or` the way pandas does before parsing.

    pandas gives `&` and `|` the low precedence of and/or (`a > 1 & b < 2` means
    `(a > 1) and (b < 2)`), so the validated tree must group operands the same way.
    """
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return tokenize.untokenize(
        (tokenize.NAME, "and" if tok.string == "&" else "or")
        if tok.type == tokenize.OP and tok.string in ("&", "|")
        else (tok.type, tok.string)
        for tok in tokens
    )


def _validate_filter(df: pd.DataFrame, text: str, pii: list[str]) -> None:
    """Allowlist check of a registry filter before pandas evaluates it.

    Registries are written by an agent from project documents, and pandas query
    syntax can call arbitrary methods (`_uuid.to_csv(...)`), so only comparisons,
    boolean logic, simple arithmetic and a few column methods are accepted.
    """
    refused = NotComputable(
        f"filter {text!r} uses syntax the report engine does not allow — {_FILTER_HINT}"
    )
    prefix = "_haa_column_"
    while prefix in text:
        prefix = f"_{prefix}"
    backticked: dict[str, str] = {}

    def placeholder(match: re.Match[str]) -> str:
        name = f"{prefix}{len(backticked)}"
        backticked[name] = match.group(1)
        return name

    source = _BACKTICK_NAME.sub(placeholder, text)
    # pandas evaluates each line on its own and cannot parse a stray backtick safely
    if "`" in source or len(text.splitlines()) > 1:
        raise refused
    try:
        tree = ast.parse(_pandas_booleans(source.strip()).strip(), mode="eval")
    except (SyntaxError, ValueError, tokenize.TokenError) as exc:
        detail = getattr(exc, "msg", None) or "invalid syntax"
        raise NotComputable(f"filter {text!r} failed: {detail}") from None
    except RecursionError:
        raise refused from None
    names: list[str] = []
    try:
        _check_node(tree, names)
        if not _is_row_condition(tree.body):
            raise _Refused
    except (_Refused, RecursionError):
        raise refused from None
    columns = {str(c) for c in df.columns}
    pii_names = {str(c) for c in pii}
    for name in names:
        column = backticked.get(name, name)
        if column in pii_names:
            raise _pii_in_filter(text, column)
        if column not in columns:
            raise NotComputable(
                f"filter {text!r} uses {column!r}, which is not a column in the dataset "
                "— check profile_dataset"
            )


def _apply_filter(df: pd.DataFrame, query: object, pii: list[str]) -> pd.DataFrame:
    if query is None or query == "":
        return df
    text = str(query)
    for column in pii:
        if re.search(rf"(?<!\w){re.escape(str(column))}(?!\w)", text):
            raise _pii_in_filter(text, column)
    _validate_filter(df, text, pii)
    try:
        return df.query(text, local_dict={}, global_dict={})
    except Exception as exc:
        # pandas error text can quote cell values, so it is never passed on
        raise NotComputable(
            f"filter {text!r} could not be evaluated on this dataset — check with "
            "profile_dataset that its columns hold the value types it compares "
            "(.str methods need text columns)"
        ) from exc


def _distinct(df: pd.DataFrame, column: str) -> int:
    return int(df[column].dropna().nunique())


def evaluate_measure(df: pd.DataFrame, measure: dict, pii: list[str]) -> float:
    aggregation = measure.get("aggregation")
    if aggregation == "percent":
        parts: list[int] = []
        for key in ("numerator", "denominator"):
            spec = measure.get(key)
            if not isinstance(spec, dict):
                raise NotComputable(f"percent needs a {key} block")
            column = check_column(df, spec.get("field"), pii)
            parts.append(_distinct(_apply_filter(df, spec.get("filter"), pii), column))
        numerator, denominator = parts
        if denominator == 0:
            raise NotComputable("the denominator is 0 — nothing to divide by")
        return 100.0 * numerator / denominator
    column = measure.get("field")
    if column is not None:
        column = check_column(df, column, pii)
    scoped = _apply_filter(df, measure.get("filter"), pii)
    if aggregation == "count":
        return float(scoped[column].notna().sum()) if column else float(len(scoped))
    if aggregation not in ("count_unique", "sum"):
        raise NotComputable(f"unknown aggregation {aggregation!r}")
    if not column:
        raise NotComputable(f"{aggregation} needs a field")
    if aggregation == "count_unique":
        return float(_distinct(scoped, column))
    values = scoped[column]
    numeric = pd.to_numeric(values, errors="coerce")
    non_numeric = int(numeric.isna().sum() - values.isna().sum())
    if non_numeric:
        raise NotComputable(
            f"column {column!r} has {non_numeric} non-numeric values — cannot sum"
        )
    return float(numeric.sum())
