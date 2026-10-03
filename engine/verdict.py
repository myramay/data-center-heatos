"""ACT / REVIEW / ESCALATE gate combining Monte Carlo and Jev.

Precedence (agreed): ESCALATE if either probability < 0.70; else REVIEW if they
disagree by > 0.20; else ACT if both > 0.90; otherwise REVIEW. With Jev
unavailable, the same thresholds apply to the Monte Carlo probability alone.
"""

from __future__ import annotations

from engine.contracts import JevOpinion, Verdict

ACT_ABOVE = 0.90
ESCALATE_BELOW = 0.70
DISAGREE_ABOVE = 0.20


def verdict(p_monte_carlo: float, jev: JevOpinion | None) -> Verdict:
    if jev is None or not jev.available:
        if p_monte_carlo < ESCALATE_BELOW:
            return "ESCALATE"
        return "ACT" if p_monte_carlo > ACT_ABOVE else "REVIEW"

    p_jev = jev.p_supply_meets_guarantees
    if min(p_monte_carlo, p_jev) < ESCALATE_BELOW:
        return "ESCALATE"
    if abs(p_monte_carlo - p_jev) > DISAGREE_ABOVE:
        return "REVIEW"
    if p_monte_carlo > ACT_ABOVE and p_jev > ACT_ABOVE:
        return "ACT"
    return "REVIEW"
