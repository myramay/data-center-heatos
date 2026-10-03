"""Deliverable 1: site decision framework (Chelsea vs Lansing).

Weighted 1-5 scores, plus a robustness check: 100,000 Dirichlet weight draws
(alpha = weights x 40) with uniform +/-0.75 noise on every score.
"""

from __future__ import annotations

import numpy as np

CRITERIA = [
    # key, label, weight, chelsea, lansing, rationale
    ("demand_density", "Demand density", 0.10, 5, 2, "dense multi-family and commercial blocks vs dispersed rural users"),
    ("source_temperature", "Source temperature", 0.10, 3, 5, "air-cooled 27-32 C vs liquid-cooled 45-65 C"),
    ("supply_continuity", "Supply continuity", 0.10, 3, 3, "colocation tenants vs crypto-exposed flexible load"),
    ("cost_displaced", "Cost of heat displaced", 0.10, 5, 4, "Con Ed steam and oil vs propane and oil"),
    ("infrastructure_cost", "Infrastructure cost per MWh", 0.15, 3, 3, "street works in Manhattan vs long rural runs"),
    ("carbon_displaced", "Carbon displaced", 0.10, 4, 4, "steam/gas on a 0.25-0.44 t/MWh grid vs propane/oil on a cleaner grid"),
    ("local_environment", "Local air / water / noise", 0.10, 5, 3, "PM2.5 92nd percentile block vs clean rural air"),
    ("vulnerable_served", "Vulnerable people served", 0.15, 5, 2, "public housing towers next door vs no disadvantaged communities"),
    ("acceptance_leverage", "Community acceptance leverage", 0.10, 3, 5, "heat as a host-community benefit for a contested data center"),
]
ALPHA_SCALE = 40.0
SCORE_NOISE = 0.75
N_DRAWS = 100_000


def weights() -> np.ndarray:
    return np.array([c[2] for c in CRITERIA])


def scores() -> tuple[np.ndarray, np.ndarray]:
    return np.array([c[3] for c in CRITERIA], float), np.array([c[4] for c in CRITERIA], float)


def site_scores(n_draws: int = N_DRAWS, seed: int = 2026, bins: int = 40) -> dict:
    w = weights()
    ch, la = scores()
    rng = np.random.default_rng(seed)
    W = rng.dirichlet(w * ALPHA_SCALE, n_draws)
    CH = ch + rng.uniform(-SCORE_NOISE, SCORE_NOISE, (n_draws, len(w)))
    LA = la + rng.uniform(-SCORE_NOISE, SCORE_NOISE, (n_draws, len(w)))
    tc, tl = (W * CH).sum(1), (W * LA).sum(1)
    diff = tc - tl
    edges = np.linspace(min(tc.min(), tl.min()), max(tc.max(), tl.max()), bins + 1)
    dedges = np.linspace(diff.min(), diff.max(), bins + 1)
    return {
        "criteria": [{"key": k, "label": lab, "weight": wt, "chelsea": c, "lansing": l, "rationale": why}
                     for k, lab, wt, c, l, why in CRITERIA],
        "totals": {"chelsea": round(float(w @ ch), 2), "lansing": round(float(w @ la), 2)},
        "robustness": {
            "n_draws": n_draws, "alpha_scale": ALPHA_SCALE, "score_noise": SCORE_NOISE,
            "p_chelsea_wins": round(float((diff > 0).mean()), 4),
            "histogram": {"edges": np.round(edges, 3).tolist(),
                          "chelsea": np.histogram(tc, edges)[0].tolist(),
                          "lansing": np.histogram(tl, edges)[0].tolist()},
            "difference_histogram": {"edges": np.round(dedges, 3).tolist(),
                                     "counts": np.histogram(diff, dedges)[0].tolist()},
        },
        "recommended": "chelsea" if w @ ch > w @ la else "lansing",
    }
