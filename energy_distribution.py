"""
Who gets the data center's heat, how much, and in what order - with an 85% heat-reuse target.

    python energy_distribution.py          (run heat_models.py first)

Writes outputs/distribution/:
    summary.csv                          one row per site (x compute type at Site 2): reuse rate, odds of hitting 85%
    <site>_allocation_buildings.csv      every building in the radius: priority rank, tier, connected or not,
                                         heat needed, heat allocated, share of need covered (P10/P50/P90)
    <site>_allocation_hourly_by_tier.csv hourly demand and allocation per priority tier (median run)
    <site>_heat_balance_hourly.csv       hourly heat produced / recoverable / delivered / used / rejected (P10/P50/P90)
    run_log.txt

1. HEAT OUTPUT (energy balance, every hour)
   All electricity a data center uses ends up as heat. The heat it must get rid of is
       IT load + UPS / power-distribution losses + fans and pumps inside the data halls (+ lighting),
   where hourly IT load comes from heat_models.py (Site 1: fitted to 111 8th's real metered electricity,
   hour-to-hour shape learned from Google's 2019 data-center power traces; Site 2: compute-type
   utilization scenarios). Chiller / dry-cooler compressor and fan power is NOT counted: when heat is
   recovered, the heat pump does that cooling job instead.

2. HOW MUCH CAN REALISTICALLY BE TAKEN (each step is a range, sampled in the Monte Carlo)
   a. Reaches a water loop. Site 1 is a multi-tenant carrier hotel retrofit: only the share of the
      halls on central chilled-water CRAHs (50-80%) can be tapped; tenant DX / refrigerant units can't.
      Site 2 is a new build: direct-to-chip liquid (70-85% of AI IT heat), rear-door coils on the rest;
      air-cooled bitcoin miners need hot-aisle containment + air-to-water coils (40-70% captured).
   b. Source temperature. Colo chilled-water return 18-24 C (tenant SLAs keep it cold); liquid loop
      40-50 C; miner exhaust = outdoor air (5-30 C) + 12-18 K.
   c. Heat-pump plant. 4 units sized to the 90th-percentile recoverable heat (not the peak), each with
      ~10 days of planned maintenance (Apr-Oct) and 1-3 forced outages a year of 1-5 days.
      COP = 0.5 x Carnot on refrigerant temperatures (evaporator 5 K below source, condenser 3 K above
      supply); supply temperature follows outdoor reset - 80 -> 65 C at Site 1 (old steam-era buildings),
      70 -> 55 C at Site 2.
   d. Network. Pipe losses (W/m) scale with mean pipe temperature above the ground, so they are larger
      in winter. Hospitals can take only 60-80% of their heat as hot water (sterilization, humidification
      and kitchens need steam); clinics 90%.
   e. Storage, sized by what physically fits: Site 1 a 300-1,000 m3 tank (Manhattan footprint), Site 2
      5,000-20,000 m3 (rural land); usable energy = volume x 1.163 kWh/m3K x (supply - return) x 90%
      stratification, charge / discharge limited to 4 hours, standing loss 0.3-2% a day.

3. 85% REUSE TARGET
   reuse rate (= ISO/IEC 30134-6 Energy Reuse Factor) = DC heat that ends up heating a building / all
   heat the data center produced. For scale: Germany's Energy Efficiency Act requires new data centers
   to reach 10% (2026) -> 20% (2028). Buildings are connected in priority order until the network can
   absorb 85% (+3% margin); if the chain above caps reuse below 85%, it connects up to the realistic
   maximum and reports the gap. At Site 2 it adds anchors of a size that exists in practice
   (a greenhouse up to 40 ha, temperature-driven demand, plus up to 15 MW of year-round process heat)
   and reports what would be needed beyond that.

4. PRIORITY
   score = 0.45 x social value  (hospital 1.0, school 0.95 ... nightclub 0.2, parking 0.1)
         + 0.30 x people served (residents or daily users, log-scaled)
         + 0.15 x equity        (NYS Disadvantaged Community or public housing)
         + 0.10 x proximity     (less pipe, lower losses)
   Tiers from social value: 1 critical (hospitals, care homes, schools, public housing),
   2 homes and civic, 3 commercial, 4 discretionary (nightlife, gyms, storage).
   Connection rule: a building is connected in score order unless its pipe would lose more than 25%
   of the heat it receives (too remote to serve efficiently).

5. HOURLY ALLOCATION
   Each hour, available heat goes to tier 1 first, then 2, 3, 4. When a tier can't be fully served,
   it is shared by weighted water-filling: every building gets the same share of its need scaled by
   its score (share_i = min(1, theta x score_i)), so higher-priority buildings get a larger share and
   nobody in the tier is cut to zero while another is fully served. Any shortfall is met by the
   building's existing boiler / steam, so no one goes cold; the question is only whose fuel the
   data center replaces.

6. CONFIDENCE
   40 Monte Carlo runs (10 weather years x 4 data-center output draws) with sampled overheads, recovery
   shares, source temperatures, heat-pump outages, storage size and losses, and +/-10% (measured) /
   +/-40% (estimated) error on each building's annual heat.
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd

from heat_models import N_DRAWS_PER_YEAR, SITES, WEATHER_YEARS, load_weather
from transport_optimization import MW_PER_MMBTU_H, MMBTU_PER_MWH, load_site

ROOT = Path(__file__).parent
DATA = ROOT / 'heat-reuse-data' / 'data'
OUT = ROOT / 'outputs' / 'distribution'
OUT.mkdir(parents=True, exist_ok=True)
RNG = np.random.default_rng(7)

REUSE_TARGET = 0.85
DESIGN_MARGIN = 0.03            # design to 88% so most Monte Carlo runs clear 85%
N_RUNS = 40
SITE2_PHASE = 'phase1'          # 150 MW
SITE2_KINDS = ['ai_training', 'ai_inference', 'bitcoin_mining']

# ---------------- DATA-CENTER HEAT ASSUMPTIONS (ranges; design uses the midpoint) ----------------
DC = {
    # 111 8th Ave: multi-tenant carrier hotel. heat_models 'it_mw' = metered DC load without the
    # weather-driven part, i.e. IT + UPS losses + room fans + base chiller.
    'site1': dict(ups_loss=(0.04, 0.08), room_fans=(0.03, 0.06), base_chiller=(0.06, 0.12),
                  water_loop_share=(0.50, 0.80),   # halls on central chilled-water CRAHs (rest: tenant DX units)
                  rec_coil=(0.90, 0.97),           # of that, heat picked up by the coils (bypass air, leakage)
                  rec_ups=(0.30, 0.60),            # electrical rooms, often on separate DX units
                  air_c=(18, 24)),                 # chilled-water return; tenant SLAs keep it cold
    # Lake Hawkeye: new build, 150 MW facility, IT capacity 125 MW (PUE 1.2)
    'site2': dict(ups_fixed=(0.01, 0.02), ups_prop=(0.025, 0.045), fans_pumps=(0.02, 0.04),
                  misc_fixed=(0.003, 0.008), rec_liquid=(0.95, 0.99), rec_ups=(0.30, 0.60),
                  liquid_c=(40, 50),
                  liquid_share={'ai_training': (0.70, 0.85), 'ai_inference': (0.70, 0.85),
                                'bitcoin_mining': (0.0, 0.0)},           # air-cooled miners
                  rec_air={'ai_training': (0.60, 0.85), 'ai_inference': (0.60, 0.85),   # rear-door coils
                           'bitcoin_mining': (0.40, 0.70)},              # hot-aisle containment + air-water coils
                  air_c={'ai_training': (24, 30), 'ai_inference': (24, 30)},
                  miner_rise=(12, 18)),            # miner exhaust = outdoor intake (5-30 C) + this rise
}
SITE2_IT_CAP_MW = 150 / 1.2

# ---------------- HEAT-PUMP PLANT ----------------
HP_CARNOT_EFF = 0.50            # of Carnot COP on refrigerant temperatures
HP_APPROACH = (5, 3)            # K: evaporator below source, condenser above supply
HP_COP_MAX = 6.5
HP_SIZE_PCTILE = 90             # plant capacity = this percentile of design recoverable heat
HP_UNITS = 4
HP_MAINT_DAYS = 10              # planned maintenance per unit per year, scheduled Apr-Oct
HP_FORCED = dict(per_year=(1, 3), hours=(24, 120))       # forced outages per unit
SUPPLY_C = {'site1': ((-10, 80), (15, 65)), 'site2': ((-10, 70), (15, 55))}   # outdoor reset (outdoor C, supply C)
RETURN_C = {'site1': 45, 'site2': 40}
GROUND_C = 10

# ---------------- NETWORK AND STORAGE ----------------
# loss_w_per_m is at a mean pipe temperature 45 K above ground; scaled hour by hour with actual temperatures
NET = {'site1': dict(loss_w_per_m=32.5, detour=1.30), 'site2': dict(loss_w_per_m=32.5, detour=1.42)}
LOSS_REF_K = 45
MAX_LOSS_SHARE = 0.25           # don't connect a building whose own pipe loses >25% of the heat it gets
                                # (= linear heat density below ~1.1 MWh per metre of pipe per year)
STORE = {'site1': dict(volume_m3=(300, 1000), loss_per_day=(0.01, 0.02)),     # what fits in Manhattan
         'site2': dict(volume_m3=(5000, 20000), loss_per_day=(0.003, 0.008))}  # rural land
STORE_DT_K = (25, 35)           # supply - return
STORE_USABLE = 0.90             # stratification / mixing
STORE_HOURS_TO_FILL = 4         # charge / discharge power = capacity / 4 h
# Share of a building's heat that a hot-water network can replace (rest stays on its own steam / CHP)
ACCEPT_SHARE = {'Hospital': 0.70, 'Clinic / outpatient': 0.90, 'Clinic / health': 0.90}
HH_SIZE = {'site1': 1.9, 'site2': 2.3}      # persons per household (Manhattan, Tompkins County)

# ---------------- SITE 2 ANCHORS (sizes that exist in practice) ----------------
GREENHOUSE_MAX_HA = 40          # among the largest heated glasshouse complexes in North America
PROCESS_MAX_MW = 15             # year-round aquaculture / food processing / drying
GERMAN_ERF = {2026: 0.10, 2027: 0.15, 2028: 0.20}   # EnEfG minimum reuse for new data centers

# ---------------- PRIORITY ----------------
WEIGHTS = dict(value=0.45, people=0.30, equity=0.15, proximity=0.10)
TIER_CUTS = [(0.90, 1), (0.70, 2), (0.40, 3), (0.0, 4)]
TIER_NAMES = {1: 'critical (health, care, schools, public housing)', 2: 'homes and civic',
              3: 'commercial', 4: 'discretionary (nightlife, gyms, storage)'}

# Site 1 (LL84 / PLUTO use type): regex -> (social value 0-1, ft2 per daily user, label). First match wins.
SITE1_USES = [
    (r'hospital|surgical', 1.00, 300, 'Hospital'),
    (r'residential care|senior|asylum|nursing', 1.00, 400, 'Care home / senior housing'),
    (r'urgent care|outpatient|medical office|health care|clinic', 0.90, 150, 'Clinic / outpatient'),
    (r'k-12|pre-school|daycare|vocational', 0.95, 100, 'School / daycare'),
    (r'dormitory|residence hall', 0.85, 300, 'Dormitory'),
    (r'education|college|university', 0.80, 150, 'College / education'),
    (r'police|fire station|courthouse|government', 0.75, 250, 'Public safety / government'),
    (r'library|community center|social/meeting', 0.70, 100, 'Library / community centre'),
    (r'religious|worship', 0.60, 60, 'Place of worship'),
    (r'one-family|two-family', 0.80, None, 'Housing'),
    (r'multifamily|apartment|walk-up|elevator|condominium|mixed residential|lodging/residential', 0.85, None, 'Housing'),
    (r'supermarket|grocery', 0.55, 150, 'Grocery'),
    (r'museum|performing arts', 0.45, 100, 'Museum / performing arts'),
    (r'office|financial|technology|laboratory', 0.45, 250, 'Office / lab'),
    (r'hotel', 0.40, 500, 'Hotel'),
    (r'store|retail|mall|mixed use', 0.40, 200, 'Retail'),
    (r'restaurant|bar|food service', 0.20, 60, 'Restaurant / bar / nightlife'),
    (r'theatre|theater|movie|arena|entertainment|assembly|recreation|fitness|gym', 0.20, 60,
     'Entertainment / gym'),
    (r'data center', 0.30, 1000, 'Data center'),
    (r'manufactur|industrial|factory|warehouse|distribution|storage|vehicle|garage|parking', 0.10, 1000,
     'Industrial / storage / parking'),
    (r'', 0.35, 300, 'Other'),
]


def site2_use(code, category):
    """NYS ORPS property class -> (social value, ft2 per daily user or None for housing, housing units, label)."""
    c = int(code) if pd.notna(code) and str(code)[:3].isdigit() else 0
    c = int(str(c)[:3]) if c else 0
    if category == 'Farm residence' or 100 <= c < 200:
        return 0.80, None, 1, 'Farm residence'
    if 210 <= c < 220 or 240 <= c < 260 or 270 <= c < 280:
        return 0.80, None, 1, 'Housing'
    if 220 <= c < 230:
        return 0.80, None, 2, 'Housing'
    if 230 <= c < 240:
        return 0.80, None, 3, 'Housing'
    if 260 <= c < 270:
        return 0.30, None, 1, 'Seasonal home'
    if 280 <= c < 290:
        return 0.80, None, 2, 'Housing'
    if c == 411:
        return 0.85, None, 0, 'Apartments'
    if 414 <= c <= 418:
        return 0.40, 500, 0, 'Hotel / inn'
    if c in (424, 425, 426):
        return 0.20, 60, 0, 'Bar / nightclub'
    if 420 <= c < 430:
        return 0.25, 60, 0, 'Restaurant'
    if 430 <= c < 450:
        return 0.15, 1000, 0, 'Vehicle / storage / warehouse'
    if c == 454:
        return 0.55, 150, 0, 'Grocery'
    if 450 <= c < 460:
        return 0.40, 200, 0, 'Retail'
    if 460 <= c < 470:
        return 0.45, 250, 0, 'Office / bank'
    if 480 <= c < 490:
        return 0.45, 250, 0, 'Mixed commercial'
    if 470 <= c < 480:
        return 0.35, 300, 0, 'Other commercial'
    if 520 <= c < 530:
        return 0.35, 60, 0, 'Theatre / assembly'
    if c == 534:
        return 0.50, 100, 0, 'Social organisation'
    if 500 <= c < 600:
        return 0.20, 100, 0, 'Recreation / sports'
    if c in (613, 615):
        return 0.80, 150, 0, 'College'
    if 610 <= c < 620:
        return 0.95, 100, 0, 'School'
    if 620 <= c < 630:
        return 0.60, 60, 0, 'Place of worship'
    if c == 633:
        return 1.00, 400, 0, 'Care home / senior housing'
    if 630 <= c < 640:
        return 0.80, 300, 0, 'Social services'
    if c == 641:
        return 1.00, 300, 0, 'Hospital'
    if 640 <= c < 650:
        return 0.90, 150, 0, 'Clinic / health'
    if 650 <= c < 670:
        return 0.75, 250, 0, 'Government / public safety'
    if 670 <= c < 680:
        return 0.60, 300, 0, 'Correctional'
    if 680 <= c < 700:
        return 0.45, 100, 0, 'Cultural / community'
    return 0.15, 1000, 0, 'Industrial / utility / other'


def draw(ranges, rng):
    """Midpoint (design) or a random draw (Monte Carlo) of each (lo, hi) range."""
    return {k: (v if not isinstance(v, tuple) else (sum(v) / 2 if rng is None else rng.uniform(*v)))
            for k, v in ranges.items()}


def section(t):
    msg = '\n' + '=' * 78 + '\n' + t + '\n' + '=' * 78
    print(msg)
    LOG.append(msg)


def log(msg=''):
    print(msg)
    LOG.append(msg)


LOG = []


# ======================================================================
# 1. DATA-CENTER HEAT OUTPUT
# ======================================================================
def supply_temp(site, t_out):
    """Network supply temperature (C), outdoor reset."""
    (tc, sc), (tw, sw) = SUPPLY_C[site]
    return np.interp(t_out, [tc, tw], [sc, sw])


def cop(source_c, sink_c):
    """Heat-pump COP: fraction of Carnot on refrigerant temperatures (with heat-exchanger approaches)."""
    te = source_c - HP_APPROACH[0] + 273.15
    tc = sink_c + HP_APPROACH[1] + 273.15
    return np.clip(HP_CARNOT_EFF * tc / np.maximum(tc - te, 5), 1.5, HP_COP_MAX)


def hp_availability(rng):
    """Share of heat-pump plant capacity available each hour (units off for maintenance or forced outages)."""
    up = np.ones((HP_UNITS, 8760))
    for u in range(HP_UNITS):
        day = 120 + u * 45 if rng is None else rng.integers(91, 304 - HP_MAINT_DAYS)    # Apr-Oct
        up[u, day * 24:(day + HP_MAINT_DAYS) * 24] = 0
        if rng is not None:
            for _ in range(rng.poisson(rng.uniform(*HP_FORCED['per_year']))):
                s = rng.integers(0, 8760)
                up[u, s:s + int(rng.uniform(*HP_FORCED['hours']))] = 0
    a = up.mean(0)
    if rng is None:                                   # design case: expected forced-outage share
        a *= 1 - np.mean(HP_FORCED['per_year']) * np.mean(HP_FORCED['hours']) / 8760
    return a


def heat_balance(site, sup, temps, kind='ai_training', run=None, rng=None, hp_cap=None):
    """
    Hourly heat balance (MW). run=None -> design case (median IT load, median weather, midpoint params).
    Returns heat_out (all waste heat produced), recoverable (reaches a water loop), captured (taken by the
    heat-pump plant, limited by its size and availability), delivered (after heat pumps, at network
    temperature), hp_elec, and the plant size.
    """
    pick = (lambda a: np.median(a, axis=0)) if run is None else (lambda a: a[run])
    t_out = np.median(temps, axis=0) if run is None else temps[run // N_DRAWS_PER_YEAR]
    if site == 'site1':
        p = draw(DC['site1'], rng)
        base = pick(sup['it_mw'])
        it = base / (1 + p['ups_loss'] + p['room_fans'] + p['base_chiller'])
        air, ups = it * (1 + p['room_fans']), it * p['ups_loss']
        heat_out = air + ups
        streams = [(air * p['water_loop_share'] * p['rec_coil'] + ups * p['rec_ups'], np.full(8760, p['air_c']))]
    else:
        cfg = DC['site2']
        p = draw({k: v for k, v in cfg.items() if not isinstance(v, dict)}, rng)
        sub = lambda key: draw({'v': cfg[key][kind]}, rng)['v']
        share, rec_air = sub('liquid_share'), sub('rec_air')
        air_c = (np.clip(t_out, 5, 30) + p['miner_rise']) if kind == 'bitcoin_mining' \
            else np.full(8760, sub('air_c'))
        it = pick(sup[f'{SITE2_PHASE}|{kind}|it_mw'])
        on = it > 0.02 * SITE2_IT_CAP_MW                       # fixed losses vanish in a full outage
        ups = (p['ups_fixed'] * SITE2_IT_CAP_MW * on + p['ups_prop'] * it)
        fans = p['fans_pumps'] * it
        misc = p['misc_fixed'] * SITE2_IT_CAP_MW * on
        liquid, air = it * share, it * (1 - share) + fans
        heat_out = liquid + air + ups + misc
        streams = [(liquid * p['rec_liquid'], np.full(8760, p['liquid_c'])),
                   (air * rec_air + ups * p['rec_ups'], air_c)]
    recoverable = sum(s for s, _ in streams)
    if hp_cap is None:
        hp_cap = float(np.percentile(recoverable, HP_SIZE_PCTILE))
    captured = np.minimum(recoverable, hp_cap * hp_availability(rng))
    frac = captured / np.maximum(recoverable, 1e-9)
    sink = supply_temp(site, t_out)
    delivered, hp_elec = np.zeros(8760), np.zeros(8760)
    for src, src_c in streams:
        c = cop(src_c, sink)
        delivered += src * frac * c / (c - 1)
        hp_elec += src * frac / (c - 1)
    return dict(heat_out=heat_out, recoverable=recoverable, captured=captured, delivered=delivered,
                hp_elec=hp_elec, hp_cap=hp_cap, sink=sink)


def pipe_loss_mw(site, pipe_m, bal):
    """Hourly network heat loss: W/m scales with mean pipe temperature above the ground."""
    mean_t = (bal['sink'] + RETURN_C[site]) / 2
    return NET[site]['loss_w_per_m'] * pipe_m / 1e6 * np.clip(mean_t - GROUND_C, 0, None) / LOSS_REF_K


def store_params(site, rng):
    """Hot-water tank: usable MWh = volume x 1.163 kWh/m3K x delta-T x stratification."""
    p = draw(STORE[site], rng)
    dt = draw({'d': STORE_DT_K}, rng)['d']
    cap = p['volume_m3'] * 1.163 * dt * STORE_USABLE / 1000
    return dict(cap=cap, power=cap / STORE_HOURS_TO_FILL, keep=(1 - p['loss_per_day']) ** (1 / 24),
                volume_m3=p['volume_m3'])


def storage(avail, demand, st):
    """Hourly thermal store: surplus charges it, deficit discharges it (power- and size-limited, with
    standing losses). Returns heat available to the network and MWh discharged over the year."""
    out = avail.copy()
    soc, discharged = 0.0, 0.0
    for t in range(len(avail)):
        soc *= st['keep']
        gap = demand[t] - avail[t]
        if gap < 0:
            c = min(-gap, st['cap'] - soc, st['power'])
            soc += c
            out[t] = avail[t] - c
        else:
            x = min(gap, soc, st['power'])
            soc -= x
            discharged += x
            out[t] = avail[t] + x
    return out, discharged


def dc_share(bal):
    """Hourly share of delivered heat that came from the data center (rest is heat-pump electricity)."""
    return bal['captured'] / np.maximum(bal['delivered'], 1e-9)


def reuse_rate(bal, used):
    """Share of all heat the DC produced that ended up heating buildings."""
    return (used * dc_share(bal)).sum() / bal['heat_out'].sum()


# ======================================================================
# 2. BUILDINGS AND PRIORITY
# ======================================================================
def prepare_buildings(site):
    b, shapes, sup = load_site(site)
    if site == 'site1':
        use = b['use_type'].fillna('').str.lower()
        rows = []
        for u, units, gfa, nycha, school in zip(use, b['res_units'].fillna(0), b['gfa_ft2'].fillna(0),
                                                 b['is_nycha'], b['is_school']):
            v, ft2, label = next((v, f, l) for pat, v, f, l in SITE1_USES if re.search(pat, u))
            if nycha:
                v, label = 0.95, 'Public housing (NYCHA)'
            elif school and v < 0.95:
                v, ft2, label = 0.95, 100, 'School / daycare'
            if ft2 is None:                                           # housing: residents + any ground-floor shops
                people = max(units, 1) * HH_SIZE[site] + max(gfa - units * 900, 0) / 200 * (units > 0)
            else:
                people = gfa / ft2 + units * HH_SIZE[site]
            rows.append((v, people, label))
        b[['social_value', 'people', 'use_label']] = pd.DataFrame(rows, index=b.index)
        b['equity'] = (b['is_dac'].astype(bool) | b['is_nycha'].astype(bool)).astype(float)
    else:
        rows = []
        for code, cat, ft2_total in zip(b['PROP_CLASS'], b['category'], b['floor_ft2'].fillna(0)):
            v, ft2, units, label = site2_use(code, cat)
            if ft2 is None:
                units = units or max(round(ft2_total / 900), 1)
                people = units * HH_SIZE[site]
            else:
                people = ft2_total / ft2
            rows.append((v, people, label))
        b[['social_value', 'people', 'use_label']] = pd.DataFrame(rows, index=b.index)
        b['is_dac'] = site2_dac(b)
        b['equity'] = b['is_dac'].astype(float)
    radius = SITES[site]['radius_m']
    b['proximity'] = (1 - b['dist_m'] / radius).clip(0, 1)
    b['people_norm'] = np.log1p(b['people']) / np.log1p(b['people'].max())
    b['priority_score'] = (WEIGHTS['value'] * b['social_value'] + WEIGHTS['people'] * b['people_norm']
                           + WEIGHTS['equity'] * b['equity'] + WEIGHTS['proximity'] * b['proximity'])
    b['tier'] = [next(t for cut, t in TIER_CUTS if v >= cut) for v in b['social_value']]
    b['annual_mwh'] = b['annual_mmbtu'] / MMBTU_PER_MWH
    b['accept_share'] = b['use_label'].map(ACCEPT_SHARE).fillna(1.0)
    b['name'] = b['name'].fillna(b['address'])
    b = b.sort_values(['priority_score', 'annual_mwh'], ascending=False).reset_index(drop=True)
    b['priority_rank'] = np.arange(1, len(b) + 1)
    return b, shapes, sup


def site2_dac(b):
    import geopandas as gpd
    from shapely import wkt
    dac = pd.read_csv(DATA / 'equity' / 'nys_dac_2023.csv', usecols=['the_geom', 'County', 'DAC_Designation'])
    dac = dac[dac['County'].str.contains('Tompkins', na=False)]
    dac = gpd.GeoDataFrame(dac, geometry=dac['the_geom'].apply(wkt.loads), crs=4326)
    pts = gpd.GeoDataFrame(index=b.index, geometry=gpd.points_from_xy(b['lon'], b['lat']), crs=4326)
    j = gpd.sjoin(pts, dac[['DAC_Designation', 'geometry']], how='left', predicate='within')
    j = j[~j.index.duplicated()]
    return j['DAC_Designation'].eq('Designated as DAC').reindex(b.index).fillna(False).to_numpy()


def connection_order(site, b):
    """
    Walk buildings in priority order; connect each one to the nearest already-connected node unless that pipe
    would lose more than MAX_LOSS_SHARE of the building's heat. Returns connected positions (in order),
    their marginal pipe length (m), and a reason for every skipped building.
    """
    net = NET[site]
    xy_ = b[['x', 'y']].to_numpy()
    near = np.hypot(xy_[:, 0], xy_[:, 1])                   # distance to the energy centre
    order, length = [], []
    status = np.array(['too remote: pipe loss > 25% of its heat'] * len(b), dtype=object)
    loss_mwh_per_m = net['loss_w_per_m'] * 8760 / 1e6
    for i in range(len(b)):
        pipe = near[i] * net['detour']
        if pipe * loss_mwh_per_m > MAX_LOSS_SHARE * b.at[i, 'annual_mwh']:
            continue
        order.append(i)
        length.append(pipe)
        status[i] = 'eligible'
        near = np.minimum(near, np.hypot(xy_[:, 0] - xy_[i, 0], xy_[:, 1] - xy_[i, 1]))
    return np.array(order), np.array(length), status


# ======================================================================
# 3. HOURLY DEMAND
# ======================================================================
def shape(shapes, bt, comp, year):
    a = shapes[f'{bt}|{comp}']
    return np.median(a, axis=0) if year is None else a[year]


def greenhouse_w_per_m2(temps):
    """Heated glasshouse demand (W/m2), every weather year: U x (set point - outdoor), energy screens at
    night, daytime solar offset, plus a dehumidification base. ~450-550 kWh/m2/yr in upstate NY."""
    h = np.arange(8760) % 24
    day = (h >= 8) & (h < 18)
    t_set = np.where(day, 18.0, 16.0)
    u = np.where(day, 5.5, 4.0)
    solar = np.where(day, 0.6, 1.0)
    return u * np.clip(t_set - temps, 0, None) * solar + 10.0


def add_anchor_shapes(shapes, temps):
    """Hourly shapes (mean 1) for new anchors, so demand_matrix can treat them like any building."""
    gh = greenhouse_w_per_m2(temps)
    ones = np.ones_like(gh)
    shapes.update({'greenhouse|space': gh / gh.mean(), 'greenhouse|dhw': ones,
                   'flat|space': ones, 'flat|dhw': ones})
    return gh


def demand_matrix(sel, shapes, year=None, err=None):
    """(n_buildings, 8760) hourly heat demand in MW."""
    ann = sel['annual_mmbtu'].to_numpy() * (1 if err is None else err)
    dhw = sel['dhw_share'].to_numpy()
    D = np.zeros((len(sel), 8760), dtype=np.float32)
    for bt in sel['nrel_type'].unique():
        m = (sel['nrel_type'] == bt).to_numpy()
        s, d = shape(shapes, bt, 'space', year), shape(shapes, bt, 'dhw', year)
        D[m] = (ann[m] / 8760 * MW_PER_MMBTU_H)[:, None] * (dhw[m, None] * d + (1 - dhw[m, None]) * s)
    return D


def prefix_demand(cand, shapes):
    """Function k -> hourly MW of the first k connected buildings (design year), via per-type prefix sums."""
    parts = []
    for bt in cand['nrel_type'].unique():
        m = (cand['nrel_type'] == bt).to_numpy()
        a = (cand['annual_mmbtu'] * cand['accept_share']).to_numpy() * m / 8760 * MW_PER_MMBTU_H
        parts.append((np.concatenate([[0], np.cumsum(a * cand['dhw_share'].to_numpy())]),
                      np.concatenate([[0], np.cumsum(a * (1 - cand['dhw_share'].to_numpy()))]),
                      shape(shapes, bt, 'dhw', None), shape(shapes, bt, 'space', None)))
    return lambda k: sum(cd[k] * sd + cs[k] * ss for cd, cs, sd, ss in parts)


# ======================================================================
# 4. HOURLY ALLOCATION
# ======================================================================
def weighted_fill(d, w, cap, iters=40):
    """Share cap (per hour) among buildings: share_i = min(1, theta * w_i), theta solved by bisection per hour."""
    lo = np.zeros(d.shape[1])
    hi = np.full(d.shape[1], 1 / w.min())
    for _ in range(iters):
        mid = (lo + hi) / 2
        got = (d * np.minimum(1, mid[None, :] * w[:, None])).sum(0)
        over = got > cap
        hi = np.where(over, mid, hi)
        lo = np.where(over, lo, mid)
    return d * np.minimum(1, lo[None, :] * w[:, None])


def allocate(avail, D, tier, w):
    """Serve tier 1 first, then 2, 3, 4; weighted water-filling inside a tier that can't be fully served."""
    alloc = np.zeros_like(D)
    rem = avail.astype(np.float64).copy()
    for t in sorted(np.unique(tier)):
        m = tier == t
        d = D[m]
        need = d.sum(0)
        full = rem >= need
        a = np.where(full[None, :], d, 0)
        short = np.flatnonzero(~full & (rem > 1e-9))
        if short.size:
            a[:, short] = weighted_fill(d[:, short], w[m], rem[short])
        alloc[m] = a
        rem = np.maximum(rem - a.sum(0), 0)
    return alloc


# ======================================================================
# 5. DESIGN: CONNECT ENOUGH DEMAND TO REUSE 85%
# ======================================================================
def network_eval(bal, D, pipe_m, site, st):
    loss = pipe_loss_mw(site, pipe_m, bal)
    avail, _ = storage(np.maximum(bal['delivered'] - loss, 0), D, st)
    used = np.minimum(avail, D)
    return reuse_rate(bal, used), avail, loss


def size_heat_pumps(site, bal, D, pipe_m):
    """Plant size (MW of source heat): the smaller of the 90th-percentile recoverable heat and what the
    connected users can take at their 99th-percentile hour (plus pipe losses), so no plant sits idle."""
    useful = (D + pipe_loss_mw(site, pipe_m, bal)) * dc_share(bal)
    return float(min(bal['hp_cap'], np.percentile(useful, 99)))


def max_reuse(bal):
    """Ceiling on reuse if every captured MWh found a user: captured / produced."""
    return bal['captured'].sum() / bal['heat_out'].sum()


def bisect_min(fn, lo, hi, goal, n=30):
    """Smallest x in [lo, hi] with fn(x) >= goal, for increasing fn."""
    for _ in range(n):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if fn(mid) < goal else (lo, mid)
    return hi


def choose_connections(site, b, shapes, bal):
    """
    Smallest priority-ordered prefix of eligible buildings that reaches the target. If the supply chain caps
    reuse below the target, connect up to (realistic maximum - 0.5 points) instead.
    """
    order, length, status = connection_order(site, b)
    if len(order) == 0:
        return order, 0.0, status, 0.0, np.nan
    cand = b.iloc[order].reset_index(drop=True)
    cum_len = np.concatenate([[0], np.cumsum(length)])
    Dk = prefix_demand(cand, shapes)
    st = store_params(site, None)
    f = lambda k: network_eval(bal, Dk(k), cum_len[k], site, st)[0]
    grid = np.unique(np.geomspace(1, len(cand), 60).astype(int))
    scores = np.array([f(k) for k in grid])
    best = scores.max()
    goal = min(REUSE_TARGET + DESIGN_MARGIN, best - 0.005)
    hi = grid[np.argmax(scores >= goal)]
    lo = grid[max(np.argmax(scores >= goal) - 1, 0)]
    while hi - lo > 1:                                   # integer bisection to the smallest k
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if f(mid) < goal else (lo, mid)
    k = hi
    status[order[k:]] = 'not connected: heat already fully used by higher-priority buildings'
    status[order[:k]] = 'connected'
    return order[:k], cum_len[k], status, f(k), best


def size_anchors(site, bal, D_local, pipe_m, gh_mw_per_ha):
    """
    Site 2: add anchors of sizes that exist in practice - a greenhouse first (up to GREENHOUSE_MAX_HA), then
    year-round process heat (up to PROCESS_MAX_MW) - until the design goal is met. Also returns the flat
    load that would be needed with no size limit (for reference).
    """
    st = store_params(site, None)
    pipe = pipe_m + 400 * NET[site]['detour']
    f = lambda ha, mw: network_eval(bal, D_local + ha * gh_mw_per_ha + mw, pipe, site, st)[0]
    big = float(bal['delivered'].max()) * 1.5
    goal = min(REUSE_TARGET + DESIGN_MARGIN, f(0, big) - 0.005)
    if f(GREENHOUSE_MAX_HA, 0) >= goal:
        ha, mw = bisect_min(lambda h: f(h, 0), 0, GREENHOUSE_MAX_HA, goal), 0.0
    else:
        ha = GREENHOUSE_MAX_HA
        mw = bisect_min(lambda m: f(ha, m), 0, PROCESS_MAX_MW, goal) if f(ha, PROCESS_MAX_MW) >= goal \
            else PROCESS_MAX_MW
    unlimited_mw = bisect_min(lambda m: f(0, m), 0, big, goal)
    return ha, mw, f(ha, mw), unlimited_mw, goal


def anchor_rows(ha, mw, gh_w_m2, b):
    rows = []
    base = dict(dhw_share=0.0, social_value=0.40, people=0.0, uncertainty=0.15, tier=3, accept_share=1.0,
                priority_score=WEIGHTS['value'] * 0.40 + WEIGHTS['proximity'], dist_m=400.0, x=400.0, y=0.0,
                equity=0.0, record_type='anchor')
    if ha > 0:
        mwh = ha * 1e4 * gh_w_m2.mean() * 8760 / 1e6
        rows.append(dict(base, id='NEW_GREENHOUSE', name=f'NEW heated greenhouse ({ha:.0f} ha)',
                         use_label='New greenhouse', nrel_type='greenhouse', annual_mwh=mwh,
                         annual_mmbtu=mwh * MMBTU_PER_MWH, priority_rank=len(b) + 1, status='connected (new anchor)'))
    if mw > 0:
        rows.append(dict(base, id='NEW_PROCESS', name=f'NEW year-round process heat ({mw:.0f} MW: aquaculture / '
                                                      f'food processing / drying)',
                         use_label='New process heat', nrel_type='flat', annual_mwh=mw * 8760,
                         annual_mmbtu=mw * 8760 * MMBTU_PER_MWH, priority_rank=len(b) + 2,
                         status='connected (new anchor)'))
    return pd.DataFrame(rows)


# ======================================================================
# 6. MONTE CARLO
# ======================================================================
CHAIN = ['produced', 'reaches_water_loop', 'within_heat_pump_capacity', 'after_pipe_losses', 'used_by_buildings']


def monte_carlo(site, conn, sup, shapes, temps, pipe_m, hp_cap, kind='ai_training'):
    """Re-run heat balance + storage + allocation for N_RUNS draws; per-building coverage and system reuse rate."""
    runs = np.linspace(0, sup_runs(site, sup, kind) - 1, N_RUNS).astype(int)
    unc = conn['uncertainty'].fillna(0.4).to_numpy() if 'uncertainty' in conn else np.full(len(conn), 0.4)
    accept = conn['accept_share'].to_numpy()[:, None]
    tier, w = conn['tier'].to_numpy(), conn['priority_score'].to_numpy()
    win = np.r_[0:59 * 24, 334 * 24:8760]                        # Dec-Feb hours
    rec = dict(reuse=[], cover=[], cover_winter=[], cover_worst=[], alloc_mwh=[], bal=[], tier=[], chain=[],
               store=[])
    for r in runs:
        bal = heat_balance(site, sup, temps, kind, run=r, rng=RNG, hp_cap=hp_cap)
        st = store_params(site, RNG)
        err = 1 + RNG.uniform(-1, 1, len(conn)) * unc
        D = demand_matrix(conn, shapes, year=r // N_DRAWS_PER_YEAR, err=err)    # full heat need
        Dacc = D * accept                                                        # part hot water can replace
        loss = pipe_loss_mw(site, pipe_m, bal)
        net = np.maximum(bal['delivered'] - loss, 0)
        avail, discharged = storage(net, Dacc.sum(0), st)
        A = allocate(avail, Dacc, tier, w)
        used = A.sum(0)
        share = dc_share(bal)
        rec['reuse'].append(reuse_rate(bal, used))
        rec['cover'].append(A.sum(1) / np.maximum(D.sum(1), 1e-9))
        rec['cover_winter'].append(A[:, win].sum(1) / np.maximum(D[:, win].sum(1), 1e-9))
        peak = D.sum(0).argmax()
        rec['cover_worst'].append(A[:, peak] / np.maximum(D[:, peak], 1e-9))
        rec['alloc_mwh'].append(A.sum(1))
        rec['chain'].append([bal['heat_out'].sum(), bal['recoverable'].sum(), bal['captured'].sum(),
                             (net * share).sum(), (used * share).sum()])
        rec['store'].append([st['volume_m3'], st['cap'], discharged, discharged / max(st['cap'], 1e-9)])
        rec['bal'].append(dict(heat_out=bal['heat_out'], recoverable=bal['recoverable'], captured=bal['captured'],
                               delivered=bal['delivered'], used=used, loss=loss,
                               rejected=np.maximum(net - used, 0)))
        rec['tier'].append({t: (D[tier == t].sum(0), A[tier == t].sum(0)) for t in np.unique(tier)})
    return {k: (np.array(v) if k not in ('bal', 'tier') else v) for k, v in rec.items()}


def sup_runs(site, sup, kind):
    return (sup['it_mw'] if site == 'site1' else sup[f'{SITE2_PHASE}|{kind}|it_mw']).shape[0]


def write_outputs(site, b, conn, mc, label=''):
    tag = f'{site}{label}'
    pc = lambda a, q: np.percentile(a, q, axis=0)
    conn = conn.assign(
        dc_heat_mwh_p50=pc(mc['alloc_mwh'], 50),
        share_of_need_covered_p10=pc(mc['cover'], 10), share_of_need_covered_p50=pc(mc['cover'], 50),
        share_of_need_covered_p90=pc(mc['cover'], 90),
        winter_share_covered_p50=pc(mc['cover_winter'], 50),
        peak_hour_share_covered_p50=pc(mc['cover_worst'], 50))
    keep = ['priority_rank', 'status', 'id', 'name', 'address', 'use_label', 'tier', 'priority_score',
            'social_value', 'people', 'equity', 'dist_m', 'annual_mwh']
    new = conn[conn['id'].astype(str).str.startswith('NEW_')]
    allb = pd.concat([b, new], ignore_index=True) if len(new) else b
    out = allb[[c for c in keep if c in allb]].merge(
        conn[['priority_rank', 'dc_heat_mwh_p50', 'share_of_need_covered_p10', 'share_of_need_covered_p50',
              'share_of_need_covered_p90', 'winter_share_covered_p50', 'peak_hour_share_covered_p50']],
        on='priority_rank', how='left')
    out['tier_name'] = out['tier'].map(TIER_NAMES)
    out.round(3).to_csv(OUT / f'{tag}_allocation_buildings.csv', index=False)

    idx = pd.date_range('2023-01-01', periods=8760, freq='h')
    hb = {'timestamp': idx}
    for k in ['heat_out', 'recoverable', 'captured', 'delivered', 'loss', 'used', 'rejected']:
        a = np.vstack([x[k] for x in mc['bal']])
        for q in (10, 50, 90):
            hb[f'{k}_mw_p{q}'] = pc(a, q)
    pd.DataFrame(hb).round(3).to_csv(OUT / f'{tag}_heat_balance_hourly.csv', index=False)

    med = int(np.argsort(mc['reuse'])[len(mc['reuse']) // 2])
    ht = {'timestamp': idx}
    for t, (d, a) in mc['tier'][med].items():
        ht[f'tier{t}_demand_mw'], ht[f'tier{t}_allocated_mw'] = d, a
    pd.DataFrame(ht).round(3).to_csv(OUT / f'{tag}_allocation_hourly_by_tier.csv', index=False)

    ch = mc['chain'] / 1000                                   # GWh of data-center heat at each step
    pd.DataFrame({'step': CHAIN, 'gwh_p10': pc(ch, 10), 'gwh_p50': pc(ch, 50), 'gwh_p90': pc(ch, 90),
                  'share_of_produced_p50': np.median(ch / ch[:, :1], axis=0)}) \
        .round(3).to_csv(OUT / f'{tag}_heat_chain.csv', index=False)
    return conn


def describe(site, conn, mc, bal, label=''):
    heat = np.vstack([x['heat_out'] for x in mc['bal']])
    log(f"Data-center heat produced: mean {heat.mean():.1f} MW, P10 hour {np.percentile(heat, 10):.1f}, "
        f"P90 hour {np.percentile(heat, 90):.1f} ({heat.sum(1).mean() / 1000:.0f} GWh/yr)")
    log(f"Heat-pump plant {bal['hp_cap']:.1f} MW of source heat ({HP_UNITS} units), mean COP "
        f"{(bal['delivered'].sum() / bal['hp_elec'].sum()):.2f}")
    ch = np.median(mc['chain'], axis=0)
    log('Where the heat goes (P50, share of heat produced): ' +
        ' -> '.join(f"{k.replace('_', ' ')} {v / ch[0]:.0%}" for k, v in zip(CHAIN, ch)))
    s = np.median(mc['store'], axis=0)
    log(f"Thermal store: {s[0]:,.0f} m3 = {s[1]:.0f} MWh usable ({s[1] / bal['delivered'].mean():.1f} h of output); "
        f"shifts {s[2] / 1000:.1f} GWh/yr ({s[3]:.0f} full cycles)")
    r = mc['reuse']
    log(f"REUSE RATE{label}: P50 {np.median(r):.1%} (P10 {np.percentile(r, 10):.1%}, P90 {np.percentile(r, 90):.1%}); "
        f"P(reuse >= {REUSE_TARGET:.0%}) = {(r >= REUSE_TARGET).mean():.0%}")
    t = conn.assign(cov=np.median(mc['cover'], 0), win=np.median(mc['cover_winter'], 0),
                    mwh=np.median(mc['alloc_mwh'], 0))
    g = t.groupby('tier').agg(buildings=('id', 'size'), people=('people', 'sum'), need_GWh=('annual_mwh', 'sum'),
                              dc_heat_GWh=('mwh', 'sum'), avg_share_covered=('cov', 'mean'),
                              winter_share_covered=('win', 'mean'))
    g[['need_GWh', 'dc_heat_GWh']] /= 1000
    g.index = [f'{i} {TIER_NAMES[i]}' for i in g.index]
    log(g.round(2).to_string())
    return dict(reuse_p10=np.percentile(r, 10), reuse_p50=np.median(r), reuse_p90=np.percentile(r, 90),
                p_reuse_ge_target=(r >= REUSE_TARGET).mean(), buildings=len(conn),
                people_served=conn['people'].sum(), heat_out_gwh=heat.sum(1).mean() / 1000,
                recoverable_share=ch[1] / ch[0], captured_share=ch[2] / ch[0], hp_plant_mw=bal['hp_cap'],
                store_mwh=s[1])


# ======================================================================
# MAIN
# ======================================================================
if __name__ == '__main__':
    pd.set_option('display.width', 200)
    summary = []
    for site in ['site1', 'site2']:
        section(f"{site.upper()}: {SITES[site]['name']}")
        b, shapes, sup = prepare_buildings(site)
        temps = load_weather(SITES[site]['station'], WEATHER_YEARS).to_numpy().reshape(len(WEATHER_YEARS), 8760)
        gh_w_m2 = add_anchor_shapes(shapes, temps)
        gh_mw_per_ha = np.median(gh_w_m2, axis=0) * 1e4 / 1e6
        kinds = [None] if site == 'site1' else SITE2_KINDS
        for kind in kinds:
            k_ = kind or 'ai_training'
            label = '' if kind is None else f'_{kind}'
            if kind:
                log(f'\n--- compute type: {kind} ---')
            bal = heat_balance(site, sup, temps, k_)
            sel, pipe_m, status, design_reuse, best = choose_connections(site, b, shapes, bal)
            bb = b.assign(status=status)
            conn = bb.iloc[sel].copy()
            log(f"Realistic ceiling on reuse (every captured MWh used): {max_reuse(bal):.1%}"
                + ('' if np.isnan(best) else f"; best the buildings in the radius can absorb: {best:.1%}"))
            log(f"{len(b):,} buildings in radius; {(~pd.Series(status).str.startswith('too remote')).sum():,} "
                f"close enough to serve efficiently; {len(conn):,} connected in priority order "
                f"({pipe_m / 1000:.1f} km of pipe); design reuse {design_reuse:.1%}")
            ha = mw = unlimited = np.nan
            if site == 'site2' and design_reuse < min(REUSE_TARGET, max_reuse(bal) - 0.02):
                D_local = (demand_matrix(conn, shapes) * conn['accept_share'].to_numpy()[:, None]).sum(0)
                ha, mw, with_anchor, unlimited, goal = size_anchors(site, bal, D_local, pipe_m, gh_mw_per_ha)
                log(f"Existing buildings can't absorb the heat. Realistic anchors: {ha:.0f} ha greenhouse + "
                    f"{mw:.0f} MW year-round process heat -> design reuse {with_anchor:.1%} (goal {goal:.1%}). "
                    f"For reference, a flat load of {unlimited:.0f} MW would be needed with no size limit.")
                conn = pd.concat([conn, anchor_rows(ha, mw, gh_w_m2, b)], ignore_index=True)
                pipe_m += 400 * NET[site]['detour']
            ceiling = max_reuse(bal)
            D_design = (demand_matrix(conn, shapes) * conn['accept_share'].to_numpy()[:, None]).sum(0)
            bal = heat_balance(site, sup, temps, k_, hp_cap=size_heat_pumps(site, bal, D_design, pipe_m))
            mc = monte_carlo(site, conn, sup, shapes, temps, pipe_m, bal['hp_cap'], k_)
            conn = write_outputs(site, bb, conn, mc, label)
            row = describe(site, conn, mc, bal, label)
            summary.append(dict(site=site, compute=k_, reuse_ceiling=ceiling, greenhouse_ha=ha,
                                process_mw=mw, flat_mw_needed_unlimited=unlimited, pipe_km=pipe_m / 1000, **row))
            top = conn.sort_values('priority_rank').head(12)
            log('Top of the priority list:')
            log(top[['priority_rank', 'name', 'use_label', 'tier', 'people', 'priority_score',
                     'share_of_need_covered_p50', 'winter_share_covered_p50']].round(2).to_string(index=False))
    s = pd.DataFrame(summary)
    s.round(3).to_csv(OUT / 'summary.csv', index=False)
    section('SUMMARY')
    log(s.round(3).to_string(index=False))
    (OUT / 'run_log.txt').write_text('\n'.join(LOG), encoding='utf-8')
    print(f'\nOutputs in {OUT}')
