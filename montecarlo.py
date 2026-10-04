"""
Monte Carlo robustness: re-solve the plan many times with randomly drawn prices and costs
(ranges in config.MC_RANGES) and count how often each building is chosen.

Buildings chosen in most scenarios are safe bets; ones that appear rarely depend on prices.
Writes out/montecarlo.json (one row per scenario) so teammates can plug in their own draws.
"""
import json
from pathlib import Path

import numpy as np

import config


def run_mc(n=None, seed=1, verbose=True, site="chelsea", fixed=None, write=True):
    from optimize import OUT, run_plan          # imported here to avoid a circular import
    from sites import get_site
    base = config.get_config(site=site)
    n = n or base["mc_runs"]
    rng = np.random.default_rng(seed)
    runs, count = [], {}
    for k in range(n):
        draw = {name: float(rng.triangular(lo, mode, hi)) for name, (lo, mode, hi) in base["mc_ranges"].items()}
        draw = {**draw, **(fixed or {})}                 # fixed values (e.g. a given grant share) beat random draws
        r = run_plan(draw, out=None, verbose=False, light=True, site=site)
        picked = {int(r["c"]["bbl"].iloc[i]) for i in r["chosen"]}
        for b in picked:
            count[b] = count.get(b, 0) + 1
        o = r["optimized"]
        party_npv = {row["id"]: row["npv_usd"] for row in r.get("ledger", [])}
        runs.append({"party_npv": party_npv, "inputs": draw, "n_buildings": o["n_buildings"], "capex_usd": o["capex_usd"],
                     "annual_value_usd": o["annual_value_usd"], "co2_avoided_t": o["co2_avoided_t"],
                     "pipe_m": o["pipe_m"], "buildings": sorted(picked)})
        if verbose and (k + 1) % 10 == 0:
            print(f"  Monte Carlo {k + 1}/{n}")
    pct = lambda key, q: float(np.percentile([x[key] for x in runs], q))
    summary = {
        "n_runs": n, "seed": seed,
        "value_usd_p10_p50_p90": [round(pct("annual_value_usd", q)) for q in (10, 50, 90)],
        "capex_usd_p10_p50_p90": [round(pct("capex_usd", q)) for q in (10, 50, 90)],
        "co2_avoided_t_p10_p50_p90": [round(pct("co2_avoided_t", q)) for q in (10, 50, 90)],
        "n_buildings_p10_p50_p90": [round(pct("n_buildings", q)) for q in (10, 50, 90)],
        "frequency": {str(b): round(c / n, 3) for b, c in count.items()},
        "party_win_pct": {pid: round(100 * float(np.mean([x["party_npv"].get(pid, 0) >= 0 for x in runs])), 1)
                          for pid in sorted({k for x in runs for k in x["party_npv"]})},
        "runs": runs,
    }
    if not write:
        return summary
    OUT.mkdir(exist_ok=True)
    Path(OUT / get_site(site).montecarlo_file).write_text(json.dumps(summary, separators=(",", ":")))
    return summary
