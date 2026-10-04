"""
Reliability: how HeatOS protects (1) the data center's cooling and (2) the continuity of heat to customers,
and how supply and demand are matched on the five axes the brief asks for.

Everything here is simple arithmetic on the plan, with failure rates in config.py, so it is easy to explain:
  * COOLING FIREWALL   heat export is a SECOND sink. The data center keeps its own cooling plant sized for
                       100% of its heat, and a fail-open bypass sends all the heat back to it in seconds.
  * CONTINUITY         every customer keeps its boiler as backup. Expected time on the backup comes from
                       failure rates (data center trip, pipe break on the path, building equipment).
  * FIVE-WAY MATCH     temperature, capacity, timing, seasonality, continuity: one number set per axis.
  * PIPE-BREAK MAP     for each street pipe: which customers sit downstream and lose network heat if it breaks.
"""
import numpy as np

from demand import DAYS


def _avail(lam, hours):
    """Unavailability (fraction of the year) from an event rate and event duration."""
    return lam * hours / 8760.0


def pipe_serves(S, c, econ, tree):
    """edge id -> {customers downstream, share of delivered heat}. Used for the pipe-break view."""
    total = float(sum(econ["deliv_kwh"].iloc[i] for i in S)) or 1.0
    out = {}
    for i in S:
        for e in tree.path_edges(tree.attach[i]):
            d = out.setdefault(e, {"serves": [], "kwh": 0.0})
            d["serves"].append(int(c["bbl"].iloc[i]))
            d["kwh"] += float(econ["deliv_kwh"].iloc[i])
    return {e: {"serves": v["serves"], "heat_share": v["kwh"] / total} for e, v in out.items()}


def building_reliability(i, econ, tree, cfg):
    """Expected network availability for one customer, and hours a year spent on its backup boiler."""
    st = cfg["storage_hours"]
    km = (tree.dist[tree.attach[i]] + float(econ["lateral_m"].iloc[i])) / 1000.0
    u_dc = _avail(cfg["dc_trips_per_year"], max(cfg["dc_trip_hours"] - st, 0.0))         # storage rides through short trips
    u_pipe = _avail(cfg["pipe_failures_per_km_year"] * km, cfg["pipe_repair_hours"])      # a break on its own path
    u_hp = _avail(cfg["hp_failures_per_year"], cfg["hp_repair_hours"])
    u = 1 - (1 - u_dc) * (1 - u_pipe) * (1 - u_hp)
    return {"availability_pct": round(100 * (1 - u), 2), "backup_hours_year": round(u * 8760, 1),
            "path_km": round(km, 2), "unavailability": {"data_center": u_dc, "pipe_on_path": u_pipe, "building_equipment": u_hp}}


def cooling_firewall(cfg, cap_kw, draw, site):
    """Numbers and rules that show the plan never leans on the data center's cooling being gone."""
    production = cap_kw / min(cfg["dc_availability"], cfg["max_export_fraction"])      # [12,24] kW the data center makes
    share = draw / np.maximum(production, 1e-9)
    peak_share = float(share.max())
    rules = [
        {"rule": "Export is a second sink, never the only one",
         "how": "The data center keeps its own heat-rejection plant (%s) sized for 100%% of its heat, with its own redundancy. "
                "HeatOS adds a free extra place for the heat to go. Nothing about its cooling design depends on us."
                % ("existing chillers and towers" if site == "chelsea" else "air-cooled dry coolers, as designed")},
        {"rule": "Cooling-first bypass",
         "how": "A fail-open valve sits between the data center's loop and the network. If the network trips, loses flow or runs hot, the valve "
                "sends all the water back through the data center's own coolers within about %d seconds. The data center's temperature setpoint is never "
                "changed by us." % cfg["bypass_response_s"]},
        {"rule": "Never export more than %d%% of the heat" % round(100 * cfg["max_export_fraction"]),
         "how": "Its own coolers always carry a base load and stay warm and tested. In this plan the peak hour exports %.1f%% of the heat, so the rule is %s."
                % (100 * peak_share, "met" if peak_share <= cfg["max_export_fraction"] else "NOT met: reduce the plan")},
        {"rule": "Separate water loops",
         "how": "A plate heat exchanger keeps the data center's coolant apart from the network water, so a pipe leak outside cannot reach the computers."},
        {"rule": "The network returns cooler water",
         "how": "Customers take heat out, so the water that comes back is cooler than what went out. That is free pre-cooling for the data center "
                "and the reason the plan frees cooling electricity (see the data-center benefits)."}]
    return {"rules": rules, "peak_export_mw": round(float(draw.max()) / 1000, 2), "peak_export_share_of_heat": round(peak_share, 4),
            "annual_export_share_of_heat": round(float((draw.sum(axis=1) * DAYS).sum() / (production.sum(axis=1) * DAYS).sum()), 4),
            "own_cooling_margin": 1.0, "max_export_fraction": cfg["max_export_fraction"], "rule_met": bool(peak_share <= cfg["max_export_fraction"]),
            "bypass_response_s": cfg["bypass_response_s"]}


def match_scorecard(S, c, econ, cfg, cap_kw, draw, optimized, cop_bm, rel):
    """The five matches the brief asks for, as numbers: temperature, capacity, timing, seasonality, continuity."""
    S = sorted(S)
    w = econ["deliv_kwh"].values[S]
    wsum = max(float(w.sum()), 1e-9)
    direct = float((econ["direct_share"].values[S] * w).sum() / wsum)
    avg_cop = float((econ["fit_cop_avg"].values[S] * w).sum() / wsum)
    jan, jul = draw[0], draw[6]
    lf_w = float(jan.mean() / max(jan.max(), 1e-9))
    supply_mw = float(cap_kw.mean()) / 1000
    peak = float(draw.max()) / 1000
    dhw = float((econ["dhw_share"].values[S] * w).sum() / wsum)
    avail = float((np.array([rel[i]["availability_pct"] for i in S]) * w).sum() / wsum)
    boost = 100 * (1 - direct)
    src = cfg["dc_source_temp_c"]
    return [
        {"axis": "Temperature", "headline": ("All of the heat is used directly: no heat pump needed" if direct >= 0.995 else
                                             "Every customer lifts the heat with a heat pump, at an average COP of %.1f" % avg_cop if direct < 0.005 else
                                             "%.0f%% of heat needs no heat pump; the rest is lifted at COP %.1f" % (100 * direct, avg_cop)),
         "numbers": {"source_temp_c": src, "direct_use_share": round(direct, 3), "average_cop": round(avg_cop, 2),
                     "hot_water_needs_c": cfg["dhw_supply_temp_c"]},
         "how": "The data center's water is at about %d C. Low-temperature users (greenhouses, radiant floors, new buildings, mild-weather radiators) take it directly. "
                "Hot water (%d C) and cold-weather radiators get a heat pump that lifts only the difference, so the less lift, the less electricity." % (src, cfg["dhw_supply_temp_c"])},
        {"axis": "Capacity", "headline": "Peak export %.1f MW against %.1f MW available (%.1f%%)" % (peak, supply_mw, 100 * peak / max(supply_mw, 1e-9)),
         "numbers": {"peak_export_mw": round(peak, 2), "supply_mw": round(supply_mw, 2), "peak_share": round(peak / max(supply_mw, 1e-9), 4)},
         "how": "Checked for every hour of 12 typical days: the heat customers can draw never exceeds what the data center can give. "
                "Each heat pump covers only part of its building's coldest hour; the boiler covers the rest."},
        {"axis": "Timing", "headline": "Winter export is %.0f%% flat across the day (average / peak)" % (100 * lf_w),
         "numbers": {"winter_load_factor": round(lf_w, 3), "winter_peak_mw": round(float(jan.max()) / 1000, 2),
                     "winter_night_mw": round(float(jan.min()) / 1000, 2)},
         "how": "Computers make heat all day. Homes peak morning and evening, offices by day, hotels and hot-water users run through the night. "
                "Mixing them flattens the draw so it matches a flat supply; the tiers decide who goes first if it ever does not."},
        {"axis": "Seasonality", "headline": "%.0f%% of the data center's heat is used in winter, %.0f%% in summer; %.0f%% of delivered heat is year-round load" % (
            100 * optimized["dc_utilization_winter"], 100 * optimized["dc_utilization_summer"], 100 * dhw),
         "numbers": {"winter_use": round(optimized["dc_utilization_winter"], 3), "summer_use": round(optimized["dc_utilization_summer"], 3),
                     "hot_water_share": round(dhw, 3)},
         "how": "Heating demand follows the weather; hot water and process heat (such as a greenhouse) do not. Customers are chosen with their summer baseload in mind, "
                "so the summer heat is used rather than wasted."},
        {"axis": "Continuity", "headline": "%.2f%% expected network availability; every customer keeps its boiler" % avail,
         "numbers": {"availability_pct": round(avail, 2), "storage_hours": cfg["storage_hours"], "backup_start_minutes": cfg["backup_start_minutes"]},
         "how": "Expected time off the network comes from failure rates (data center trip, a break on the customer's own pipe path, building equipment). "
                "Storage rides through short trips, valves isolate a broken branch, and the customer's boiler starts by itself within about %d minutes. "
                "Protected and firm customers are the last to be cut when supply is short." % cfg["backup_start_minutes"]},
    ]
