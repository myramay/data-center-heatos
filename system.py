"""
The heat-reuse SYSTEM, in engineering terms: where the heat is captured and how much, how it travels
(flow, pipe sizes, pumping, losses), how each customer takes it, what has to be built, how it is run month
by month, and where the energy goes. All numbers come from the plan; assumptions are in config.py.
"""
import math

import numpy as np

from demand import DAYS, FUELS, KBTU_PER_KWH

USE_NOTES = {   # why each kind of customer suits waste heat (qualitative; the numbers come from the plan)
    "Multifamily Housing": "Big, steady hot-water load all year plus winter heating; many residents per connection.",
    "Hotel": "Hot water around the clock in every season, the best match for a flat heat source.",
    "Office": "Daytime heating in the cold months; low-temperature systems in newer buildings.",
    "K-12 School": "Winter daytime heating; closed in summer, so a seasonal customer.",
    "Hospital (General Medical & Surgical)": "Heat and hot water 24/7 and a continuity-critical use.",
    "Greenhouse (proposed)": "Low-temperature heat needed in every month; takes the 50 C water directly.",
    "Retail Store": "Daytime heating in winter; small load.",
    "Worship Facility": "Part-time heating; small load.",
    "Municipal/Emergency": "Must stay warm; continuity-critical.",
}


def pick_dn(flow_m3s, cfg):
    """Smallest standard pipe whose water speed stays under the design limit."""
    for dn in cfg["pipe_sizes_dn"]:
        d = dn / 1000.0 * 0.92                              # inner diameter a bit under nominal
        v = flow_m3s / (math.pi / 4 * d * d)
        vmax = next(vm for lim, vm in cfg["pipe_velocity_limits"] if dn <= lim)
        if v <= vmax:
            return dn, v
    return cfg["pipe_sizes_dn"][-1], v


def flow_kg_s(kw, cfg):
    dt = cfg["dc_source_temp_c"] - cfg["loop_return_c"]
    return kw / (cfg["water_cp_kj_per_kg_k"] * dt)


def system_block(S, c, econ, delivered, dc_draw, demand, tree, cfg, cap_kw, opt, site, pipes_by_edge):
    S = sorted(S)
    dt = cfg["dc_source_temp_c"] - cfg["loop_return_c"]
    rho = 985.0                                                     # kg/m3 (warm water)
    production = cap_kw / min(cfg["dc_availability"], cfg["max_export_fraction"])        # [12,24] kW the data center makes
    draw = dc_draw[S].sum(axis=0)                                   # [12,24] kW exported
    peak_kw = float(draw.max())

    # ---- pipe sizes: every street segment carries the draw of all customers downstream of it
    acc = {}
    for i in S:
        for e in tree.path_edges(tree.attach[i]):
            acc[e] = acc.get(e, 0) + dc_draw[i]
    sizing, dn_km = {}, {}
    for e, arr in acc.items():
        kw = float(arr.max()) * cfg["design_peak_diversity"]
        m = flow_kg_s(kw, cfg)
        dn, v = pick_dn(m / rho, cfg)
        sizing[e] = {"dn_mm": dn, "flow_kg_s": round(m, 1), "peak_draw_kw": round(kw), "velocity_m_s": round(v, 2)}
        dn_km[dn] = dn_km.get(dn, 0) + tree.edge_len[e] / 1000
    lat_sizing = {}
    for i in S:
        kw = float(dc_draw[i].max())
        m = flow_kg_s(kw, cfg)
        dn, v = pick_dn(max(m, 1e-6) / rho, cfg)
        lat_sizing[i] = {"dn_mm": dn, "flow_kg_s": round(m, 2)}
        dn_km[dn] = dn_km.get(dn, 0) + float(econ["lateral_m"].iloc[i]) / 1000
    total_m = float(flow_kg_s(peak_kw, cfg))
    link_dn, link_v = pick_dn(total_m / rho, cfg)
    dn_km[link_dn] = dn_km.get(link_dn, 0) + tree.dc_to_root_m / 1000

    # ---- pumping: head along the longest path (supply + return) plus a substation; power follows the cube of flow
    crit_m = max(tree.dist[tree.attach[i]] + float(econ["lateral_m"].iloc[i]) for i in S) + tree.dc_to_root_m
    dp_pa = cfg["pipe_pressure_drop_pa_per_m"] * 2 * crit_m + cfg["substation_pressure_drop_kpa"] * 1000
    p_peak_kw = (total_m / rho) * dp_pa / cfg["pump_efficiency"] / 1000
    rel = draw / max(peak_kw, 1e-9)
    pump_mwh = p_peak_kw * float(((rel ** 3).sum(axis=1) * DAYS).sum()) / 1000.0

    # ---- heat loss from the pipe walls, computed (the optimizer assumes a fixed share)
    trench_m = sum(float(tree.edge_len[e]) for e in acc) + float(econ["lateral_m"].iloc[S].sum()) + tree.dc_to_root_m
    t_mean = (cfg["dc_source_temp_c"] + cfg["loop_return_c"]) / 2
    loss_kw = cfg["pipe_loss_w_per_m_per_k"] * 2 * trench_m * (t_mean - cfg["ground_temp_c"]) / 1000
    loss_mwh = loss_kw * 8760 / 1000
    central_mean = (cfg["central_network_supply_c"] + cfg["central_network_return_c"]) / 2
    loss_factor = (central_mean - cfg["ground_temp_c"]) / max(t_mean - cfg["ground_temp_c"], 1.0)
    delivered_mwh = float(econ["deliv_kwh"].iloc[S].sum()) / 1000

    # ---- storage tank at the network head
    avg_winter_kw = float(draw[[11, 0, 1]].mean())
    tank_m3 = avg_winter_kw * cfg["storage_hours"] * 3600 / (cfg["water_cp_kj_per_kg_k"] * rho * cfg["storage_delta_t_k"])

    # ---- how each customer takes the heat
    e_S = econ.iloc[S]
    direct_mask = e_S["direct_share"].values >= 0.8
    iface = {"direct_exchanger": {"n": int(direct_mask.sum()), "kw_th": round(float(e_S["hp_kw"].values[direct_mask].sum()))},
             "heat_pump": {"n": int((~direct_mask).sum()), "kw_th": round(float(e_S["hp_kw"].values[~direct_mask].sum()))}}
    n_branch = sum(1 for e in acc if sum(1 for x in acc if tree.pred.get(x) == e) > 1)

    # ---- month by month operating picture
    dem = demand[S]                                                  # [n,12,24] kW wanted
    rows = []
    peak_wanted = max(float(dem[:, m].sum() * DAYS[m]) / 1000 for m in range(12)) or 1.0
    for m in range(12):
        dm = float(dem[:, m].sum() * DAYS[m]) / 1000
        dl = float(delivered[S][:, m].sum() * DAYS[m]) / 1000
        ex = float(draw[m].sum() * DAYS[m]) / 1000
        mk = float(production[m].sum() * DAYS[m]) / 1000
        share = ex / max(mk, 1e-9)
        rows.append({"month": m + 1, "heat_wanted_mwh": round(dm), "heat_delivered_mwh": round(dl), "network_covers_pct": round(100 * dl / max(dm, 1e-9)),
                     "dc_heat_made_mwh": round(mk), "dc_heat_exported_mwh": round(ex), "share_of_dc_heat_used_pct": round(100 * share, 1),
                     "boiler_mwh": round(dm - dl),
                     "mode": ("Winter: heat pumps run longest and boilers trim the coldest hours" if dm >= 0.6 * peak_wanted else
                              "Shoulder: the network covers most of the heat" if dm >= 0.25 * peak_wanted else
                              "Summer: only the year-round loads run (hot water, greenhouse baseload)")})
    # heat-pump COP by month, weighted by heat delivered
    cop_m = []
    for m in range(12):
        wk = delivered[S][:, m].sum(axis=1) * DAYS[m]
        cop_m.append(round(float((np.array([econ["cop_m"].iloc[i][m] for i in S]) * wk).sum() / max(wk.sum(), 1e-9)), 2))
    for m, r in enumerate(rows):
        r["average_cop"] = cop_m[m]

    # ---- where the energy goes (GWh per year)
    made = float(production.sum(axis=1).dot(DAYS)) / 1e6
    exported = float(opt["dc_heat_used_gwh"])
    hp_elec = float(e_S["hp_elec_kwh"].sum()) / 1e6
    fuel = {f: float(e_S[f"avoid_kbtu_{f}"].sum()) / KBTU_PER_KWH / 1e6 for f in FUELS}
    fuel = {f: round(v, 2) for f, v in fuel.items() if v > 0.005}
    delivered_gwh = float(e_S["deliv_kwh"].sum()) / 1e6
    energy = {"data_center_heat_made_gwh": round(made, 1), "exported_gwh": round(exported, 1), "rejected_by_data_center_gwh": round(made - exported, 1),
              "heat_pump_electricity_gwh": round(hp_elec, 2), "pumping_electricity_gwh": round(pump_mwh / 1000, 2), "network_loss_gwh": round(loss_mwh / 1000, 2),
              "delivered_to_customers_gwh": round(delivered_gwh, 1), "fuel_displaced_gwh": fuel, "fuel_displaced_total_gwh": round(sum(fuel.values()), 1),
              "heat_per_kwh_of_electricity": round(delivered_gwh / max(hp_elec + pump_mwh / 1000, 1e-9), 1),
              "share_of_data_center_heat_reused_pct": round(100 * exported / max(made, 1e-9), 1)}
    if "cluster_homes" in c:                  # Lansing: homes in neighborhood customers
        units = int(c["n_units"].iloc[S][c["cluster_homes"].iloc[S].fillna(False).astype(bool)].sum())
    else:                                      # Chelsea: residential units on the tax lots
        units = int(c["unitsres"].iloc[S].fillna(0).sum()) if "unitsres" in c else 0
    return {
        "source": {"capture_point": site.capture_point, "supply_c": cfg["dc_source_temp_c"], "return_c": cfg["loop_return_c"], "delta_t_k": dt,
                   "heat_made_mw": round(float(production.mean()) / 1000, 1), "exported_peak_mw": round(peak_kw / 1000, 2),
                   "exported_average_mw": round(float(draw.mean()) / 1000, 2), "share_exported_at_peak_pct": round(100 * peak_kw / max(float(production.max()), 1e-9), 1),
                   "heat_made_basis": cfg.get("_supply_info")},
        "network": {"type": site.architecture, "street_pipe_km": round(sum(tree.edge_len[e] for e in acc) / 1000, 2),
                    "service_pipe_km": round(float(econ["lateral_m"].iloc[S].sum()) / 1000, 2), "link_to_data_center_m": round(tree.dc_to_root_m),
                    "pipe_km_by_dn": {str(k): round(v, 2) for k, v in sorted(dn_km.items())},
                    "peak_flow_kg_s": round(total_m, 1), "link_dn_mm": link_dn, "link_velocity_m_s": round(link_v, 2),
                    "critical_path_km": round(crit_m / 1000, 2), "pump_head_kpa": round(dp_pa / 1000), "pump_peak_kw": round(p_peak_kw),
                    "pumping_mwh_year": round(pump_mwh), "pumping_pct_of_heat": round(100 * pump_mwh / max(delivered_mwh, 1e-9), 2),
                    "heat_loss_computed_pct": round(100 * loss_mwh / max(delivered_mwh, 1e-9), 2), "heat_loss_assumed_pct": round(100 * cfg["pipe_heat_loss_fraction"], 1),
                    "conventional_network_loss_factor": round(loss_factor, 1), "mean_water_temp_c": round(t_mean, 1),
                    "storage_tank_m3": round(tank_m3), "storage_hours": cfg["storage_hours"], "sectionalizing_branches": n_branch},
        "interfaces": iface,
        "bill_of_materials": [
            {"group": "At the data center", "item": "Plate heat exchanger, duty %.1f MW (two units at 60%% each for N+1)" % (peak_kw / 1000), "qty": 2},
            {"group": "At the data center", "item": "Fail-open bypass valve and tie-in to the data center's own coolers", "qty": 1},
            {"group": "At the data center", "item": "Circulation pumps, %d kW at peak (duty + standby)" % round(p_peak_kw), "qty": 2},
            {"group": "Network", "item": "Insulated street and service pipe, %.1f km in total" % (sum(dn_km.values())), "qty": round(sum(dn_km.values()), 1), "unit": "km"},
            {"group": "Network", "item": "Hot-water buffer store, %d m3 (%s hours of average winter load)" % (round(tank_m3), cfg["storage_hours"]), "qty": 1},
            {"group": "Network", "item": "Isolation valves at %d branch points and at every customer" % n_branch, "qty": n_branch + len(S)},
            {"group": "At customers", "item": "Direct heat exchangers (customers that need no heat pump), %d kW total" % iface["direct_exchanger"]["kw_th"], "qty": iface["direct_exchanger"]["n"]},
            {"group": "At customers", "item": "Heat pumps with heat exchanger, %d kW total" % iface["heat_pump"]["kw_th"], "qty": iface["heat_pump"]["n"]},
            {"group": "At customers", "item": "Heat meters and controls", "qty": len(S)},
            {"group": "At customers", "item": "Existing boiler kept as automatic backup (starts within %d minutes)" % cfg["backup_start_minutes"], "qty": len(S)}],
        "months": rows, "energy": energy,
        "households_served": units, "customers": len(S)}, sizing, lat_sizing


def why_suitable(b):
    """Plain reasons this customer suits waste heat, from its own numbers (at most four)."""
    out = []
    if b.get("proposed"):
        out.append("New customer that the cheap local heat makes possible.")
    if b["hot_water_share"] >= 0.45:
        what = "heat in every month" if b.get("proposed") else "hot water all year"
        out.append("Needs %s (%d%% of its heat is year-round load), so the data center's summer heat is used, not wasted." % (what, round(100 * b["hot_water_share"])))
    if b["temperature_match"]["direct_use_share"] >= 0.5:
        out.append("%s of its heat needs no heat pump: the water is already hot enough." % ("All" if b["temperature_match"]["direct_use_share"] >= 0.995 else "%d%%" % round(100 * b["temperature_match"]["direct_use_share"])))
    elif b["temperature_match"]["average_cop"] >= 5.0:
        out.append("Low temperature lift (heat pump COP %.1f), so little electricity per unit of heat." % b["temperature_match"]["average_cop"])
    if b["location"]["distance_m"] <= 450:
        out.append("Close to the data center (%d m), so the pipe is short." % b["location"]["distance_m"])
    if b["tier"] == "PROTECTED":
        out.append("Public housing or an affordability priority: its heat is never cut.")
    elif b["tier"] == "FIRM":
        out.append("A continuity-critical use: its heat is never cut.")
    if b["fit"]["timing"] >= 0.8:
        out.append("Steady use through the day, which matches a steady heat source.")
    if b["heat_delivered_mwh_year"] >= 5000 and len(out) < 4:
        out.append("A large load (%s MWh a year), so one connection reuses a lot of heat." % format(round(b["heat_delivered_mwh_year"]), ","))
    return out[:4]


def user_mix(chosen):
    """Customers grouped by kind: how many, how much heat, what temperature they take, and why they suit."""
    groups = {}
    for b in chosen:
        k = b["property_type"] or "Other"
        if k.startswith("Neighborhood"):
            k = "Households (neighborhoods)"
        g = groups.setdefault(k, {"kind": k, "customers": 0, "heat_mwh_year": 0.0, "hot_water_x_heat": 0.0, "direct_x_heat": 0.0, "tiers": {}})
        g["customers"] += 1
        g["heat_mwh_year"] += b["heat_delivered_mwh_year"]
        g["hot_water_x_heat"] += b["hot_water_share"] * b["heat_delivered_mwh_year"]
        g["direct_x_heat"] += b["temperature_match"]["direct_use_share"] * b["heat_delivered_mwh_year"]
        g["tiers"][b["tier"]] = g["tiers"].get(b["tier"], 0) + 1
    out = []
    for g in groups.values():
        h = max(g["heat_mwh_year"], 1e-9)
        out.append({"kind": g["kind"], "customers": g["customers"], "heat_mwh_year": round(g["heat_mwh_year"]),
                    "hot_water_share": round(g["hot_water_x_heat"] / h, 2), "direct_use_share": round(g["direct_x_heat"] / h, 2),
                    "tiers": g["tiers"], "why": USE_NOTES.get(g["kind"], "Steady heat demand close to the network.")})
    return sorted(out, key=lambda r: -r["heat_mwh_year"])
