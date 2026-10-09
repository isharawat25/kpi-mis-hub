"""Variance and RAG status - pure deterministic functions."""


def variance(actual, target):
    if actual is None or target is None:
        return None, None
    var = actual - target
    pct = None if target == 0 else round(var / abs(target) * 100, 2)
    return round(var, 4), pct


def rag_status(actual, target, direction: str, tolerance_pct: float) -> str:
    if actual is None or target is None:
        return "NA"
    if target == 0:
        good = actual >= 0 if direction == "higher_better" else actual <= 0
        return "GREEN" if good else "RED"
    gap_pct = (actual - target) / abs(target) * 100
    if direction == "lower_better":
        gap_pct = -gap_pct
    if gap_pct >= 0:
        return "GREEN"
    if gap_pct >= -abs(tolerance_pct):
        return "AMBER"
    return "RED"


def pct_change(current, prior):
    if current is None or prior is None or prior == 0:
        return None
    return round((current - prior) / abs(prior) * 100, 2)
