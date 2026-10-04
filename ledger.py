"""
Stakeholder ledger: who pays what, who gets what, who carries which risk.

For each party we compute (all from the plan, with every price in config.py):
  pays up front, pays each year, gets each year, net each year, simple payback, and net present value.
The Monte Carlo then reports how often each party comes out ahead. A deal is only 'balanced' when every
party has a high chance of winning.
"""
import numpy as np

from allocate import TIERS

NAMES = {
    "chelsea": {"dc": "111 8th Ave data center operator", "operator": "HeatOS network operator",
                "funder": "Con Edison (rate-base, UTEN Act)", "customers": "Heat customers", "community": "New York City and State",
                "funder_how": "recovers the pipe cost slowly through rates", "funder_kind": "rate-base"},
    "lansing": {"dc": "TeraWulf (Lake Hawkeye data center)", "operator": "Town of Lansing + TeraWulf heat venture",
                "funder": "State clean-heat grants", "customers": "Greenhouse, fish farm and households", "community": "Lansing and Tompkins County",
                "funder_how": "pays its share as a grant", "funder_kind": "grant"},
}


def _pv(rate, years):
    return (1 - (1 + rate) ** -years) / rate


def party_ledger(S, c, econ, cfg, opt, site_id):
    """One row per party for the chosen set S. `opt` is the evaluate() dict for the plan."""
    nm = NAMES[site_id]
    r, n = cfg["discount_rate"], cfg["ledger_years"]
    pv = _pv(r, n)
    S = sorted(S)
    rows = []
    mk = lambda pid, name, role, up, pay, get, risk, resp, extra=None: {
        "id": pid, "party": name, "role": role, "pays_upfront_usd": round(up), "pays_annual_usd": round(pay), "gets_annual_usd": round(get),
        "net_annual_usd": round(get - pay), "simple_payback_years": (round(up / (get - pay), 1) if up > 0 and get - pay > 0 else None),
        "npv_usd": round(-up + (get - pay) * pv), "risk": risk, "responsibility": resp, **(extra or {})}

    # ---- data center operator: pays for the export station; keeps the cooling electricity and water it no longer needs
    heat_gwh = opt["dc_heat_used_gwh"]
    export_kw = opt["peak_dc_draw_mw"] * 1000
    capex_dc = cfg["dc_export_usd_per_kw_th"] * export_kw
    cool_kwh = heat_gwh * 1e6 / cfg["chiller_cop"]
    water_gal = heat_gwh * 1e6 * cfg["evaporative_cooling_share"] * cfg["cooling_water_l_per_kwh_heat"] / 3.785
    dc_gets = cool_kwh * cfg["electricity_price_for_dc_usd_per_kwh"] + water_gal * cfg["water_value_usd_per_gallon"] + heat_gwh * 1000 * cfg["dc_heat_price_usd_per_mwh_th"]
    rows.append(mk("dc", nm["dc"], "Hosts the heat source", capex_dc, cfg["dc_om_fraction"] * capex_dc, dc_gets,
                   "Carries only the cost of its own connection. Its cooling is protected by the bypass and its own plant, so it takes none of the customers' risk.",
                   "Builds and maintains the export heat exchanger, pumps and bypass valve; keeps its own cooling plant independent and at 100% size.",
                   {"gets_breakdown": {"cooling_electricity_freed_gwh": round(cool_kwh / 1e6, 1), "water_saved_million_gal": round(water_gal / 1e6, 1)}}))

    # ---- the network operator (project company)
    heat_cost = heat_gwh * 1000 * cfg["dc_heat_price_usd_per_mwh_th"]
    op_net = opt["annual_net_cash_usd"] - heat_cost
    rows.append(mk("operator", nm["operator"], "Owns heat pumps and tie-ins, runs the network, sells the heat", opt["capex_usd"], 0,
                   op_net,
                   "Carries demand risk (a big customer leaves), fuel-price risk (customers' alternatives get cheaper) and electricity-price risk for the heat pumps.",
                   "Owns and runs the heat pumps, heat exchangers and meters; keeps the tier promises; refunds customers for missed guaranteed hours."))

    # ---- pipe funder: pays the share of the street pipes that the project does not
    pipe_capex_others = opt["network_cost_paid_by_others_usd"] / (cfg["discount_rate"] / (1 - (1 + cfg["discount_rate"]) ** -cfg["pipe_life_years"]) + cfg["pipe_om_fraction"]) if opt["network_cost_paid_by_others_usd"] > 0 else 0.0
    om_funder = cfg["pipe_om_fraction"] * pipe_capex_others
    scc = cfg["social_cost_carbon_usd_per_t"]
    public_value = opt["co2_avoided_t"] * scc
    crf_p = cfg["discount_rate"] / (1 - (1 + cfg["discount_rate"]) ** -cfg["pipe_life_years"])
    ratepayer_cost = 0.0
    if pipe_capex_others > 0:
        if nm["funder_kind"] == "rate-base":
            # a regulated utility earns its money back (with a return) through everyone's bills: it breaks even by design,
            # and the cost lands on ratepayers, which we show on the community line.
            annuity = pipe_capex_others * crf_p
            ratepayer_cost = annuity + om_funder
            row = mk("funder", nm["funder"], "Builds and owns the shared street pipes", pipe_capex_others, om_funder, annuity + om_funder,
                     "Regulatory risk: it only earns the return if the regulator allows the pipes into rates.",
                     "Owns and maintains the street pipes and %s." % nm["funder_how"])
            row["npv_usd"] = round(-pipe_capex_others + annuity * _pv(r, cfg["pipe_life_years"]))
        else:
            row = mk("funder", nm["funder"], "Pays for the shared street pipes", pipe_capex_others, om_funder, public_value,
                     "Capital at risk if few customers join. The benefit is public (carbon), not a cash return.",
                     "Pays its share of the street pipes as a grant.",
                     {"benefit_cost_ratio": round(public_value * pv / max(pipe_capex_others + om_funder * pv, 1), 2),
                      "public_cost_per_tonne_co2": round((pipe_capex_others / n + om_funder) / max(opt["co2_avoided_t"], 1))})
        rows.append(row)

    # ---- customers, by service tier
    sav = (econ["fuel_avoided_usd"] + econ["fines_avoided"] - econ["revenue"]).values
    for t in TIERS:
        idx = [i for i in S if econ["tier"].iloc[i] == t]
        if not idx:
            continue
        units = int(sum(c["n_units"].iloc[i] if "n_units" in c else 1 for i in idx))
        s = float(sav[idx].sum())
        rows.append(mk("customers_" + t.lower(), "%s: %s" % (nm["customers"], t.lower()), "Pays for heat only, keeps its boiler as backup", 0, 0, s,
                       "Lock-in to one heat supplier; protected by price below today's cost, backup boiler, and refunds for missed hours (firm and protected tiers).",
                       "Allows the connection, pays the heat bill, keeps its boiler serviced.",
                       {"n_customers": len(idx), "n_buildings_or_homes": units, "savings_per_unit_usd": round(s / max(units, 1))}))

    # ---- community and state
    eq_sav = float(sav[[i for i in S if bool(econ["public_housing"].iloc[i])]].sum()) if S else 0.0
    rows.append(mk("community", nm["community"], "Sets the rules, receives the public benefit", 0, ratepayer_cost, public_value,
                   "Carbon and air benefits only appear if customers actually connect.",
                   "Permits the pipes, sets the tier rules and the affordability guarantee.",
                   {"ratepayer_cost_usd_year": round(ratepayer_cost), "benefit_cost_ratio": (round(public_value / ratepayer_cost, 2) if ratepayer_cost else None),
                    "co2_avoided_t": round(opt["co2_avoided_t"]), "nox_avoided_t": round(opt.get("nox_avoided_t", 0), 2),
                    "equity_customers_savings_usd": round(eq_sav)}))
    return rows
