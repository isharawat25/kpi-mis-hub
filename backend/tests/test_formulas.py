import pytest

from app.services.formulas import FormulaError, evaluate, evaluation_order, validate_formula
from app.services.metrics import rag_status, variance


def test_evaluate_and_missing():
    assert evaluate("OPEX / REVENUE * 100", {"OPEX": 50, "REVENUE": 200}) == 25
    assert evaluate("A / B", {"A": 1, "B": 0}) is None
    assert evaluate("A + B", {"A": 1}) is None
    assert evaluate("max(A, B) - abs(C)", {"A": 1, "B": 4, "C": -2}) == 2


def test_validation_blocks_code_injection():
    for bad in ["__import__('os')", "A.real", "[1,2]", "'x'", "A if B else C", "open('f')"]:
        with pytest.raises(FormulaError):
            validate_formula(bad, {"A", "B", "C"})
    with pytest.raises(FormulaError):
        validate_formula("A + ZZZ", {"A"})


def test_circular_reference():
    with pytest.raises(FormulaError):
        evaluation_order({"X": "Y + 1", "Y": "X * 2"})
    assert evaluation_order({"X": "Y + 1", "Y": "A * 2"}) == ["Y", "X"]


def test_rag():
    assert rag_status(100, 100, "higher_better", 5) == "GREEN"
    assert rag_status(96, 100, "higher_better", 5) == "AMBER"
    assert rag_status(90, 100, "higher_better", 5) == "RED"
    assert rag_status(104, 100, "lower_better", 5) == "AMBER"
    assert rag_status(90, 100, "lower_better", 5) == "GREEN"
    assert variance(110, 100) == (10, 10.0)
