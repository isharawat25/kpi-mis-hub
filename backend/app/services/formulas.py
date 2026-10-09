"""Deterministic, sandboxed KPI formula evaluator.

Formulas reference other KPI codes, e.g. "(GROSS_PROFIT / REVENUE) * 100".
Only arithmetic, numbers, KPI codes and min/max/abs/round are allowed - no eval().
"""
import ast
import operator

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"min": min, "max": max, "abs": abs, "round": round}


class FormulaError(ValueError):
    pass


def _parse(formula: str) -> ast.Expression:
    try:
        return ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"Invalid formula syntax: {e.msg}") from e


def referenced_codes(formula: str) -> set[str]:
    tree = _parse(formula)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in _FUNCS:
            names.add(node.id)
    return names


def validate_formula(formula: str, known_codes: set[str]) -> None:
    tree = _parse(formula)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
                             ast.Call)) or type(node) in _BIN or type(node) in _UNARY:
            if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
                raise FormulaError("Only numeric constants are allowed")
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS or node.keywords:
                    raise FormulaError("Only min, max, abs, round functions are allowed")
            continue
        raise FormulaError(f"Unsupported expression element: {type(node).__name__}")
    missing = referenced_codes(formula) - known_codes
    if missing:
        raise FormulaError(f"Unknown KPI codes in formula: {', '.join(sorted(missing))}")


def evaluate(formula: str, values: dict[str, float | None]) -> float | None:
    """Return None if any referenced value is missing or a division by zero occurs."""
    tree = _parse(formula)

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            v = values.get(node.id)
            if v is None:
                raise _Missing(node.id)
            return float(v)
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            return float(_FUNCS[node.func.id](*[ev(a) for a in node.args]))
        raise FormulaError(f"Unsupported expression element: {type(node).__name__}")

    try:
        return ev(tree)
    except (_Missing, ZeroDivisionError):
        return None


class _Missing(Exception):
    pass


def evaluation_order(formulas: dict[str, str]) -> list[str]:
    """Topologically sort formula KPIs so formulas built on other formulas work."""
    order, state = [], {}

    def visit(code):
        if state.get(code) == 1:
            raise FormulaError(f"Circular formula reference involving {code}")
        if state.get(code) == 2:
            return
        state[code] = 1
        for dep in referenced_codes(formulas[code]):
            if dep in formulas:
                visit(dep)
        state[code] = 2
        order.append(code)

    for c in formulas:
        visit(c)
    return order
