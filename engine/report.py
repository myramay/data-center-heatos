"""One-click report: the five deliverables, generated from the live model.

    md = build_report("chelsea", live_sim=sim, confidence=conf, deal=deal)
    html = to_html(md); pdf = to_pdf(md)
"""

from __future__ import annotations

import io
from collections import Counter
from datetime import datetime
from pathlib import Path

import markdown as md_lib
from jinja2 import Environment, FileSystemLoader

from engine import providers
from engine.config import load_site
from engine.contracts import ConfidenceResult, SiteId
from engine.guarantees import DealReport
from engine.impact import impact
from engine.ledger import compute_ledger
from engine.recommend import plan_details
from engine.scenarios import scenarios_for
from engine.site_scoring import site_scores

TEMPLATES = Path(__file__).parent / "templates"
YEAR = datetime(2026, 1, 1)
STRESS_START = datetime(2026, 1, 12)

RISKS = {
    "chelsea": [
        ("Cold snap beyond design (1-in-50-year)", "Climate / technical", "Medium", "High",
         "Hot-water tanks + borehole storage; every building keeps its boiler or steam; guarantee premiums priced on CVaR95",
         "Con Ed", "polar_vortex"),
        ("Anchor tenant leaves 111 8th Ave", "Commercial", "Medium", "High",
         "Phase-gated build-out; heat removal fee indexed to recovered heat; storage buffers the step change",
         "Google / Con Ed", "tenant_leaves"),
        ("Data center outage", "Technical", "Low", "Medium", "Storage ride-through, then boilers; no customer loses heat",
         "Google", "server_outage"),
        ("More frequent heat waves (19 -> 69 days over 90 F by 2050)", "Climate", "High", "Medium",
         "Two-way loop absorbs office cooling load and sells it", "Con Ed", "heat_wave"),
        ("Electricity price spike", "Market", "Medium", "Medium",
         "MPC autopilot moves buildings to boilers when heat pumps cost more; all-in heat price shields buildings",
         "Con Ed", "price_spike"),
        ("Data center cooling compromised by the network", "Technical", "Very low", "Very high",
         "Hard constraint: the network only takes heat the data center offers; its own cooling stays sized for 100%",
         "Google", None),
        ("UTEN rate case / Local Law 97 changes", "Regulatory", "Medium", "High",
         "Deal clears without LL97 fines in the sensitivity runs before final investment decision (verify)", "Con Ed / NYC", None),
        ("Street works and permitting delay", "Delivery", "High", "Medium",
         "Phase by block, starting with the public housing pilot next to the data center", "Con Ed / NYC DOT", None),
        ("Building data quality", "Data", "High (today)", "Medium",
         "Replace mock inventory with LL84 + PLUTO + footprints (teammate pipeline in progress)", "Data / ML team", None),
    ],
    "lansing": [
        ("Lake-effect cold snap", "Climate / technical", "Medium", "Medium",
         "Pit storage sized for seasonal shifting; boilers kept at each user", "Joint venture", "lake_effect_cold_snap"),
        ("Crypto price crash switches off flexible load", "Market", "High", "Medium",
         "Heat contracts on the firm (non-flexible) share; pit storage bridges", "TeraWulf", "bitcoin_price_crash"),
        ("Greenhouse operator off-season or exit", "Commercial", "Medium", "Medium",
         "Pit absorbs surplus; schools and homes phase in", "Joint venture", "greenhouse_off_season"),
        ("Data center outage", "Technical", "Low", "Low", "Pit storage ride-through", "TeraWulf", "server_outage"),
        ("Electricity price spike", "Market", "Medium", "Low", "Mostly direct use; only boosters use power", "Homes co-op", "price_spike"),
        ("Community opposition to the data center", "Social", "Medium", "High",
         "Heat as a host-community benefit; tax abatement tied to heat delivery", "Town of Lansing", None),
        ("Cayuga Lake nutrient / thermal impacts", "Environmental", "Low", "High",
         "Closed loops; aquaculture effluent treated; monitoring (verify)", "Joint venture", None),
    ],
}

PHASES = {
    "chelsea": [
        ("Pilot with public housing and guaranteed buildings",
         "Connect the highest-priority buildings closest to 111 8th Ave: public housing and buildings buying heat "
         "guarantees first, with Con Ed building the first loop segment, the data center heat exchanger, and hot-water tanks."),
        ("Loop and storage expansion",
         "Extend the ambient loop along the street grid and drill the borehole field so summer heat (including "
         "cooling rejected by offices) is stored for winter."),
        ("Neighborhood infrastructure",
         "Fold the loop into Con Ed's regulated thermal utility (UTEN Act), connecting further buildings as their "
         "boilers come up for replacement."),
    ],
    "lansing": [
        ("Built-in from day one", "TeraWulf's liquid cooling exports 45-65 C water through a heat export station."),
        ("Greenhouse park and pit storage", "Greenhouses, aquaculture and schools take warm water directly; the pit on "
         "the remediated coal yard shifts summer surplus to winter."),
        ("Homes co-op", "Home clusters along the loop join a co-op; small booster heat pumps make hot tap water, "
         "financed on the bill."),
    ],
}


def _scenario_result(site: SiteId, name: str, base: dict) -> str:
    from engine.sim import Simulation

    start = datetime(2026, 7, 10) if name == "heat_wave" else STRESS_START
    sim = Simulation(site, start=start, hours=168, narrate=False)
    sim.run(24)
    sim.apply_scenario(name)
    s = sim.run().summary
    guaranteed = sim.net.guaranteed
    missed = int(((sim.results().flows.backup_kw > 0.01 * sim.inp.demand_kw[:168].clip(min=1e-9)) [:, guaranteed]).sum())
    extra = s["backup_heat_mwh"] - base.get(start, {}).get("backup_heat_mwh", 0.0)
    parts = [f"backup {extra:+,.0f} MWh/week vs normal", f"storage used {s['storage_discharged_mwh']:,.0f} MWh"]
    if name == "heat_wave":
        parts.append(f"cooling sold {s['cooling_sold_mwh']:,.0f} MWh")
    parts.append(f"guaranteed missed hours {missed}")
    return "; ".join(parts)


def build_report(site: SiteId, live_sim=None, confidence: ConfidenceResult | None = None,
                 deal: DealReport | None = None) -> str:
    from engine.sim import Simulation

    cfg = load_site(site)
    buildings = providers.get_buildings(site)
    by_id = {b.id: b for b in buildings}
    details = plan_details(site)
    plan = details.plan

    year = Simulation(site, start=YEAR, hours=8760, plan=plan, narrate=False)
    yr = year.run()
    imp = impact(year, yr)
    led = compute_ledger(year, yr, deal.terms if deal else None, deal.premiums_usd if deal else None)

    cards = providers.model_cards()
    all_mock = all(c.is_mock for c in cards.values())
    inventory: dict[str, dict] = {}
    for b in buildings:
        row = inventory.setdefault(b.heating_system, {"n": 0, "mwh": 0.0})
        row["n"] += 1
        row["mwh"] += b.annual_heat_mwh

    connected = [{"name": by_id[i.building_id].name, "use": by_id[i.building_id].use_type.replace("_", " "),
                  "option": i.option.replace("_", " "), "phase": i.phase, "npv": i.npv_usd,
                  "reasons": ", ".join(r.replace("_", " ") for r in i.reason_codes if not r.startswith("best_option"))}
                 for i in sorted((i for i in plan.items if i.connect), key=lambda i: (i.phase, -i.npv_usd))]
    nc = Counter(i.reason_codes[0] for i in plan.items if not i.connect)

    base_runs = {}
    for start in {STRESS_START, datetime(2026, 7, 10)}:
        base_runs[start] = Simulation(site, start=start, hours=168, plan=plan, narrate=False).run().summary
    names = {s.name for s in scenarios_for(site)}
    risks = []
    for risk, typ, like, imp_, mit, owner, test in RISKS[site]:
        risks.append({"risk": risk, "type": typ, "likelihood": like, "impact": imp_, "mitigation": mit, "owner": owner,
                      "test": test.replace("_", " ") if test else None,
                      "result": _scenario_result(site, test, base_runs) if test in names else None})

    phases = []
    for n, (title, text) in enumerate(PHASES[site], start=1):
        phases.append({"n": n, "title": title, "text": text,
                       "buildings": [by_id[i.building_id].name for i in plan.items if i.connect and i.phase == n]})

    p_ahead = deal.p_ahead if deal else {}
    parties = []
    for p in led.parties.values():
        p.p_ahead = p_ahead.get(p.id)
        parties.append(p)

    if deal is None:
        deal_text = "Deal check not run for this report."
    elif deal.cleared:
        deal_text = (f"Yes: every party is ahead in at least 80% of {deal.n_futures} sampled futures"
                     + (f" after these changes: {'; '.join(deal.changes)}." if deal.changes else " with the configured terms."))
    else:
        gaps = ", ".join((f"{p} short ${v:,.0f}/yr" if v >= 1 else f"{p} near break-even") for p, v in deal.gap_usd_per_yr.items())
        deal_text = (f"Not yet. After trying {deal.candidates_tried} combinations of heat-price discount, grant share and phase "
                     f"timing, {', '.join(deal.binding_parties)} remain below 80% probability of coming out ahead "
                     f"({gaps}). Best terms found: {'; '.join(deal.changes) or 'configured terms'}. "
                     "Further levers: grant support for building-side equipment, lower-cost transfer stations, or a "
                     "higher heat-removal fee.")

    if confidence:
        drivers = ", ".join(f"{d.name} {d.share:.0%}" for d in confidence.top_uncertainty_drivers[:3])
        confidence_text = (f"Over the next {confidence.horizon_hours} h, P(all connected buildings fully served by the network) = "
                           f"{confidence.p_all_warm:.0%}; expected hours on backup {confidence.expected_unmet_hours:.1f}; "
                           f"top uncertainty drivers: {drivers} ({confidence.n_futures} futures, {confidence.method}).")
    else:
        confidence_text = "Live confidence not attached to this report."

    run = None
    if live_sim is not None and live_sim.h > 0:
        run = {"run_id": live_sim.run_id or "-", "time": live_sim.inp.times[live_sim.h - 1].isoformat()}

    carbon_price = cfg.policy["carbon_price_usd_per_t"].value if "carbon_price_usd_per_t" in cfg.policy else 0.0
    provenance = {
        "Site scores": "engine/site_scoring.py: weights and scores in CRITERIA; robustness = 100,000 Dirichlet draws",
        "Recommendation": "engine/recommend.py: per-option 20-yr NPV at the site discount rate + avoided CO2 x carbon price",
        "Heat, backup, electricity": "engine/physics.py + engine/sim.py: hourly simulation with energy balance checked every hour",
        "Net CO2": "engine/impact.py: fuel displaced x fuel factor - (heat pump + pump electricity) x grid + chiller electricity avoided x grid",
        "Water": "engine/impact.py: evaporation = heat (MJ) / 2.26; tower water = evaporation / 0.67; minus 2 gal per kWh of added electricity",
        "Party economics": "engine/ledger.py: hourly money flows, capex by party (pipe = minimum spanning tree), NPV at each party's rate",
        "Everyone wins / guarantee premiums": "engine/guarantees.py: sampled futures through the simulator; premium = E[refunds] + CVaR95 margin",
        "Stress-test results": "engine/scenarios.py: 7-day runs from 12 Jan (heat wave: 10 Jul), stress fired at hour 24, rules autopilot",
    }

    env = Environment(loader=FileSystemLoader(TEMPLATES), trim_blocks=False, lstrip_blocks=False)
    return env.get_template("report.md.j2").render(
        site=cfg.site, cfg=cfg, generated=datetime.now().strftime("%Y-%m-%d %H:%M"), run=run,
        data_status=("Building inventory, weather, demand, supply and confidence currently come from MOCK providers "
                     "(teammates' data and models pending); figures are illustrative." if all_mock else
                     "Data providers: " + ", ".join(f"{k}: {'mock' if c.is_mock else c.name}" for k, c in cards.items())),
        scores=site_scores(), model_cards=cards, buildings=buildings, inventory=dict(sorted(inventory.items())),
        connected=connected, not_connected={"npv_negative": nc.get("npv_negative", 0), "capacity_limit": nc.get("capacity_limit", 0)},
        plan_used_kw=details.used_kw, plan_budget_kw=details.budget_kw, carbon_price=carbon_price,
        impact=imp, risks=risks, confidence_text=confidence_text, phases=phases, parties=parties, deal_text=deal_text,
        assumptions=list(cfg.iter_params()), provenance=provenance)


CSS = """
body { font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt; color: #111; line-height: 1.45; margin: 28px; }
h1 { font-size: 20pt; margin-bottom: 4px; } h2 { font-size: 14pt; border-bottom: 1px solid #ccc; padding-bottom: 3px; margin-top: 22px; }
h3 { font-size: 11.5pt; margin-top: 14px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 12px; font-size: 9pt; }
th, td { border: 1px solid #ccc; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #f1f3f6; } blockquote { border-left: 3px solid #e8a33d; margin: 8px 0; padding: 4px 10px; color: #444; background: #fff8ec; }
code { font-size: 8.5pt; }
"""


def to_html(markdown_text: str, title: str = "HeatOS report") -> str:
    body = md_lib.markdown(markdown_text, extensions=["tables"])
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><style>{CSS}</style></head><body>{body}</body></html>"


def to_pdf(markdown_text: str) -> bytes:
    html = to_html(markdown_text)
    try:
        from weasyprint import HTML        # preferred when system libraries are present
        return HTML(string=html).write_pdf()
    except Exception:
        from xhtml2pdf import pisa
        buf = io.BytesIO()
        result = pisa.CreatePDF(html, dest=buf, encoding="utf-8")
        if result.err:
            raise RuntimeError("PDF rendering failed")
        return buf.getvalue()
