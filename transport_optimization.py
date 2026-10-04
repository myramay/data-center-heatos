"""
Heat-transport options for each site, optimized on cost and net carbon, then stress-tested
with Monte Carlo ("does everyone stay warm, and does every party profit?").

    python transport_optimization.py      (run heat_models.py first)

Writes outputs/transport/:
    options_summary.csv          one row per site x option x objective, with Monte Carlo odds
    selected_<site>_<option>.csv buildings each optimized network connects
    mc_draws.csv                 every Monte Carlo draw (for charts / deeper analysis)

Method, in short (details in METHODOLOGY.md):
  1. DESIGN  - for each transport option, greedily connect buildings in order of heat value per
               metre of pipe, keeping a building only if it raises system value
               (fuel avoided + carbon value - pipe, equipment, electricity, O&M).
               Run twice: carbon priced at $0 (cost-optimal) and at $190/t (carbon-weighted).
  2. STRESS  - 1,000 Monte Carlo draws of weather year, data-center output, demand error, prices,
               costs, pipe failures; each draw re-dispatches the network hour by hour.
  3. ODDS    - P(every connected building gets all its heat every hour) and
               P(data center, network operator AND every buyer all come out ahead).
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
MOD = ROOT / 'outputs' / 'models'
OUT = ROOT / 'outputs' / 'transport'
OUT.mkdir(parents=True, exist_ok=True)

N_MC = 1000
MW_PER_MMBTU_H = 0.29307
MMBTU_PER_MWH = 3.41214
GAS_EF = 0.05311              # tCO2e per MMBtu of gas burned (LL97 coefficient)
BOILER_EFF = 0.85             # network backup boiler
CARBON_PRICES = {'cost-optimal': 0, 'carbon-weighted': 190}   # $/tCO2e (EPA social cost of carbon, 2023)

SITE_XY = {'site1': (40.740704, -74.001844)}   # site2 centre is read from the building table

# ---------------- PARAMETER RANGES (uniform; design uses the midpoint) ----------------
# These are planning-level ranges to VERIFY against the Danish Energy Agency technology catalogue,
# HDR cost data and utility tariffs before quoting numbers. The Monte Carlo is what makes them honest.
COMMON = {
    'discount_rate': (0.04, 0.08),
    'om_frac': (0.015, 0.03),            # O&M per year as share of capex
    'pump_frac': (0.01, 0.025),          # pumping electricity as share of heat delivered
    'buyer_discount': (0.05, 0.20),      # tariff = buyer's current heat cost x (1 - discount)
    'price_mult': (0.75, 1.35),          # fuel-price uncertainty on every buyer's current cost
    'dc_heat_price': (0.0, 2.0),         # $ per MMBtu of source heat paid to the data center
    'dc_tiein_per_kw': (40, 100),        # data-center side heat exchangers, valves, controls ($/kW source)
    'central_hp_per_kw': (500, 900),     # large heat pump at the energy centre ($/kW heat)
    'building_hp_per_kw': (900, 1600),   # 5th-gen: heat pump in each building ($/kW heat)
    'building_hp_cop': (3.2, 4.2),       # 5th-gen loop ~25 C -> 60-65 C
    'backup_boiler_per_kw': (80, 150),
    'embodied_pipe_kg_per_m': (150, 450),
    'embodied_hp_kg_per_kw': (60, 120),
    'pipe_fail_per_km_yr': (0.05, 0.20),
    'repair_hours': (12, 48),
    'll97_share_over_limit': (0.2, 0.6), # share of NYC buyers who would otherwise pay LL97 penalties
    'demand_err_measured': (-0.10, 0.10),
    'demand_err_estimated': (-0.40, 0.40),
}
SITE_PARAMS = {
    'site1': {
        'pipe_per_m': (5000, 12000),     # Manhattan: street opening, utilities congestion, restoration
        'loss_w_per_m': (25, 40),
        'detour': (1.25, 1.35),          # street grid vs straight line
        'conn_fixed': (25000, 60000), 'conn_per_kw': (40, 100),
        'retrofit_per_kw_old': (50, 250),  # low-temperature compatibility work, buildings before 1980
        'elec_per_mwh': (120, 220),
        'grid_t_per_mwh': (0.20, 0.35),
        'steam_fuel_price': (28, 45),    # Con Ed steam $/MMBtu (NOT in the data - verify against tariff)
        'backup_gas_price': (6, 11),
    },
    'site2': {
        'pipe_per_m': (700, 1500),       # rural roadside trench
        'transmission_per_m': (1200, 2500),
        'loss_w_per_m': (25, 40),
        'transmission_loss_w_per_m': (35, 55),
        'detour': (1.35, 1.5),
        'conn_fixed': (6000, 12000), 'conn_per_kw': (40, 100),
        'retrofit_per_kw_air': (300, 700),   # homes with forced hot air need a hydronic coil / new emitters
        'retrofit_per_kw_old': (50, 150),
        'elec_per_mwh': (60, 110),
        'grid_t_per_mwh': (0.06, 0.18),      # upstate NY grid is much cleaner than NYC zone J
        'backup_gas_price': (6, 11),
        'cooling_kwh_saved_per_mwh': (15, 40),   # dry-cooler fan / adiabatic energy avoided
        # third-party / new anchors
        'cornell_annual_mmbtu': (600_000, 1_000_000),   # campus heat, VERIFY with Cornell Facilities
        'cornell_x': (5, 9),                             # Cornell's own CHP heat cost $/MMBtu (assumption)
        'cornell_ef': (0.04, 0.066),
        'greenhouse_ha': (5, 20),
        'greenhouse_kwh_m2': (400, 700),
        'greenhouse_x': (9.5, 25),                       # gas or propane alternative, $/MMBtu useful
        'truck_container_mwh': (2, 4),
        'truck_cycles_per_day': (1, 3),
        'truck_per_km': (3, 6), 'truck_handling': (100, 250),
        'truck_container_capex': (150_000, 300_000),
        'truck_loss': (0.05, 0.12),
        'truck_diesel_l_per_km': (0.35, 0.45),
    },
}
LIFE = {'pipe': 40, 'hp': 20, 'other': 25}
CORNELL_LATLON = (42.4534, -76.4735)


def midpoint(ranges):
    return {k: (lo + hi) / 2 for k, (lo, hi) in ranges.items()}


def sample(ranges, rng):
    return {k: rng.uniform(lo, hi) for k, (lo, hi) in ranges.items()}


def annuity(rate, years):
    return rate / (1 - (1 + rate) ** -years)


def xy(lat, lon, lat0, lon0):
    return np.column_stack([(np.asarray(lon) - lon0) * np.cos(np.radians(lat0)) * 111_320,
                            (np.asarray(lat) - lat0) * 110_540])


# ======================================================================
# DATA
# ======================================================================
def load_site(site):
    b = pd.read_csv(MOD / f'{site}_building_demand_summary.csv', low_memory=False)
    shapes = dict(np.load(MOD / f'{site}_demand_shapes.npz'))
    sup = dict(np.load(MOD / f'{site}_supply_runs.npz'))
    b = b[(b['annual_mmbtu'] > 0) & b['lat'].notna()].copy()
    if site == 'site1':
        lat0, lon0 = SITE_XY['site1']
        b['ef'] = (b['thermal_co2_t'] / b['annual_mmbtu']).fillna(GAS_EF / 0.8)
        b['is_old'] = ~(pd.to_numeric(b['year_built'], errors='coerce') >= 1980)
        b['is_air'] = False
        b['group'] = np.where(b['record_type'] == 'LL84 measured', 'measured', 'estimated')
        n_lots = b['bbl'].astype(str).str.count(';') + 1
        b['is_campus'] = ((n_lots >= 2) | b['name'].str.contains('campus|cogen|houses', case=False, na=False)) \
            & (b['annual_mmbtu'] >= 15000) & (b['record_type'] == 'LL84 measured')
    else:
        lat0, lon0 = site2_centre()
        SITE_XY['site2'] = (lat0, lon0)
        b['ef'] = (b['thermal_co2_t'] / b['annual_mmbtu']).fillna(GAS_EF / 0.8)
        b['is_old'] = ~(pd.to_numeric(b['year_built'], errors='coerce') >= 1980)
        heat = b.get('HEAT_TYPE_DESC', pd.Series('', index=b.index)).fillna('').str.lower()
        b['is_air'] = heat.str.contains('air') | heat.str.contains('electric')
        b['group'] = 'estimated'
        b['is_campus'] = False
        b['record_type'] = 'parcel estimated'
    b[['x', 'y']] = xy(b['lat'], b['lon'], lat0, lon0)
    b['road_m'] = np.hypot(b['x'], b['y'])
    return b.reset_index(drop=True), shapes, sup


def site2_centre():
    """Centroid of the Cayuga Operating Co parcels (same definition as heat_models.py), cached."""
    cache = MOD / 'site2_centre.txt'
    if cache.exists():
        return tuple(float(v) for v in cache.read_text().split(','))
    import geopandas as gpd
    p = gpd.read_file(ROOT / 'heat-reuse-data' / 'data' / 'site2_lansing' / 'tompkins_parcels.geojson',
                      columns=['PRIMARY_OWNER']).to_crs(32618)
    site = p[p['PRIMARY_OWNER'].str.contains('Cayuga Operating', case=False, na=False)]
    c = gpd.GeoSeries([site.geometry.union_all().centroid], crs=32618).to_crs(4326).iloc[0]
    cache.write_text(f'{c.y},{c.x}')
    return c.y, c.x


def add_site2_anchors(b, shapes, p):
    """Third-party / new anchors that are not in the parcel table."""
    lat0, lon0 = SITE_XY['site2']
    cx, cy = xy([CORNELL_LATLON[0]], [CORNELL_LATLON[1]], lat0, lon0)[0]
    gh_type = 'warehouse' if 'warehouse|space' in shapes else sorted({k.split('|')[0] for k in shapes})[0]
    edu = 'primaryschool' if 'primaryschool|space' in shapes else gh_type
    anchors = pd.DataFrame([
        dict(id='ANCHOR_CORNELL', name='Cornell University central heating (third-party anchor)', category='Anchor',
             nrel_type=edu, annual_mmbtu=p['cornell_annual_mmbtu'], dhw_share=0.40,
             current_cost_X_usd_per_mmbtu=p['cornell_x'], ef=p['cornell_ef'], x=cx, y=cy,
             is_old=False, is_air=False, group='cornell', is_campus=True, record_type='anchor', peak_kw_p50=np.nan),
        dict(id='ANCHOR_GREENHOUSE', name='New greenhouse / aquaculture on adjacent farmland', category='Anchor',
             nrel_type=gh_type, dhw_share=0.10,
             annual_mmbtu=p['greenhouse_ha'] * 1e4 * p['greenhouse_kwh_m2'] / 1000 * MMBTU_PER_MWH,
             current_cost_X_usd_per_mmbtu=p['greenhouse_x'], ef=0.066, x=400.0, y=0.0,
             is_old=False, is_air=False, group='greenhouse', is_campus=True, record_type='anchor', peak_kw_p50=np.nan),
    ])
    anchors['road_m'] = np.hypot(anchors['x'], anchors['y'])
    return pd.concat([b, anchors], ignore_index=True)


# ======================================================================
# HOURLY DEMAND HELPERS
# ======================================================================
_SHAPE_CACHE = {}


def year_shape(shapes, bt, comp, year=None):
    key = (id(shapes), bt, comp, year)
    if key not in _SHAPE_CACHE:
        a = shapes[f'{bt}|{comp}']
        _SHAPE_CACHE[key] = np.median(a, axis=0) if year is None else a[year]
    return _SHAPE_CACHE[key]


def set_demand(sel, shapes, year=None):
    """Hourly MW of a set of buildings, summed type by type (fast)."""
    D = np.zeros(8760)
    for bt, g in sel.groupby('nrel_type'):
        a_d = (g['annual_mmbtu'] * g['dhw_share']).sum()
        a_s = (g['annual_mmbtu'] * (1 - g['dhw_share'])).sum()
        D += (a_d * year_shape(shapes, bt, 'dhw', year) + a_s * year_shape(shapes, bt, 'space', year)) \
            / 8760 * MW_PER_MMBTU_H
    return D


def building_mw(row, shapes, year=None):
    s, d = year_shape(shapes, row['nrel_type'], 'space', year), year_shape(shapes, row['nrel_type'], 'dhw', year)
    return row['annual_mmbtu'] / 8760 * MW_PER_MMBTU_H * (row['dhw_share'] * d + (1 - row['dhw_share']) * s)


def peak_kw(row, shapes):
    v = row.get('peak_kw_p50', np.nan)
    return v if pd.notna(v) else building_mw(row, shapes).max() * 1000


def buyer_x(b, site, p):
    """Buyer's current cost of useful heat, $/MMBtu (steam priced from assumption)."""
    x = b['current_cost_X_usd_per_mmbtu'].copy()
    if site == 'site1':
        steam = b['main_fuel'].eq('steam') & x.isna()
        x[steam] = p['steam_fuel_price'] / 0.90
    return x.fillna(7.66 / 0.8 if site == 'site1' else 13.35 / 0.8)


# ======================================================================
# OPTIONS
# ======================================================================
OPTIONS = {
    'site1': {
        '4gdh_hot_water':   dict(kind='pipe', desc='New 4th-gen hot-water network (65-70 C), central heat pump at 111 8th'),
        '5gdh_ambient':     dict(kind='pipe5', desc='5th-gen ambient loop (~25 C), heat pump in every building'),
        'campus_anchor':    dict(kind='pipe', campus_only=True,
                                 desc='Hot-water mains to existing campus plants only (NYCHA, Penn South, cogen campuses)'),
    },
    'site2': {
        '4gdh_local':       dict(kind='pipe', exclude_anchors=True, desc='New hot-water network to surrounding homes/schools'),
        '5gdh_local':       dict(kind='pipe5', exclude_anchors=True, desc='5th-gen ambient loop with home heat pumps'),
        'cornell_transmission': dict(kind='pipe', only_ids=['ANCHOR_CORNELL'], transmission=True,
                                     desc='17 km insulated transmission main to Cornell central plant (third party)'),
        'greenhouse_anchor': dict(kind='pipe', only_ids=['ANCHOR_GREENHOUSE'],
                                  desc='Co-located greenhouse / aquaculture on adjacent farmland, heat pump to 65 C'),
        'greenhouse_direct': dict(kind='direct', only_ids=['ANCHOR_GREENHOUSE'],
                                  desc='Greenhouse heated DIRECTLY by ~45 C liquid-cooling return water (no heat pump; '
                                       'air-cooled mining still needs one)'),
        'mobile_storage':   dict(kind='truck', top_n=6, exclude_anchors=True,
                                 desc='Heat-battery containers trucked to the largest distant users (schools, Cornell)'),
    },
}


def supply_arrays(site, sup, kind_key=None, run=None):
    """Delivered heat at the energy centre (MW), its heat-pump electricity, source heat and DC cooling saved."""
    if site == 'site1':
        g = lambda k: sup[k]
    else:
        g = lambda k: sup[f'phase1|{kind_key}|{k}']
    pick = (lambda a: np.median(a, axis=0)) if run is None else (lambda a: a[run])
    out = dict(delivered=pick(g('delivered_heat_mw')), hp_elec=pick(g('hp_elec_mw')),
               source=pick(g('source_heat_mw')))
    out['cooling_saved'] = pick(g('cooling_saved_mw')) if site == 'site1' else None
    return out


def design(site, opt_name, b, shapes, sup, carbon_price, kind_key='ai_training'):
    """Greedy network design at midpoint parameters. Returns selected rows + design quantities."""
    opt = OPTIONS[site][opt_name]
    p = {**midpoint(COMMON), **midpoint(SITE_PARAMS[site])}
    rate = p['discount_rate']
    cand = b.copy()
    if opt.get('campus_only'):
        cand = cand[cand['is_campus'] | (cand['dist_m'] < 1)]
    if opt.get('only_ids'):
        cand = cand[cand['id'].isin(opt['only_ids'])]
    if opt.get('exclude_anchors'):
        cand = cand[cand['record_type'] != 'anchor']
    cand = cand.assign(X=buyer_x(cand, site, p))
    # priority: heat value per metre of route from the site
    cand['priority'] = cand['annual_mmbtu'] * cand['X'] / (cand['road_m'] + 50)
    cand = cand.sort_values('priority', ascending=False).head(1500 if site == 'site1' else 5000)

    s = supply_arrays(site, sup, kind_key)
    if opt['kind'] == 'pipe5':
        cop_b = p['building_hp_cop']
        avail0 = s['source'] * cop_b / (cop_b - 1)
        elec_per_mwh_heat = 1 / cop_b
    elif opt['kind'] == 'direct':
        avail0, elec_per_mwh_heat = s['source'], 0.0
    else:
        avail0 = s['delivered']
        elec_per_mwh_heat = (s['hp_elec'].sum() / s['delivered'].sum())
    trans = opt.get('transmission', False)
    pipe_cost = p['transmission_per_m'] if trans else p['pipe_per_m']
    if opt['kind'] == 'pipe5':
        pipe_cost *= 0.7
    loss_w = 0 if opt['kind'] == 'pipe5' else (p['transmission_loss_w_per_m'] if trans else p['loss_w_per_m'])
    a_pipe, a_eq = annuity(rate, LIFE['pipe']), annuity(rate, LIFE['other'])
    a_hp = annuity(rate, LIFE['hp'])

    if opt['kind'] == 'truck':
        return design_trucks(site, opt, cand, shapes, avail0, elec_per_mwh_heat, p, carbon_price)

    nodes = [(0.0, 0.0)]
    D = np.zeros(8760)
    pipe_m, chosen, dc_prev = 0.0, [], 0.0
    misses = 0
    for _, r in cand.iterrows():
        nx = np.array(nodes)
        inc = np.hypot(nx[:, 0] - r['x'], nx[:, 1] - r['y']).min() * p['detour']
        d_i = building_mw(r, shapes)
        avail = np.maximum(avail0 - (pipe_m + inc) * loss_w / 1e6, 0)
        dc_new = np.minimum(avail, D + d_i).sum()
        marginal_dc = dc_new - dc_prev                       # MWh/yr of DC heat this building adds
        kw = peak_kw(r, shapes)
        retro = kw * (p['retrofit_per_kw_air'] if r['is_air'] else p['retrofit_per_kw_old'] if r['is_old'] else 0)
        bhp = kw * p['building_hp_per_kw'] if opt['kind'] == 'pipe5' else 0
        capex = inc * pipe_cost + p['conn_fixed'] + kw * p['conn_per_kw']
        annual_cost = (inc * pipe_cost * a_pipe + (p['conn_fixed'] + kw * p['conn_per_kw'] + retro) * a_eq
                       + bhp * a_hp + capex * p['om_frac']
                       + marginal_dc * (elec_per_mwh_heat + p['pump_frac']) * p['elec_per_mwh'])
        value = marginal_dc * MMBTU_PER_MWH * (r['X'] + carbon_price * r['ef'])
        if value > annual_cost:
            nodes.append((r['x'], r['y']))
            D += d_i
            pipe_m += inc
            dc_prev = dc_new
            chosen.append(r.name)
            misses = 0
        else:
            misses += 1
            if misses > 400 and len(chosen) > 0:
                break
    sel = cand.loc[chosen]
    return dict(sel=sel, pipe_m=pipe_m, loss_w=loss_w, pipe_cost=pipe_cost, kind=opt['kind'],
                transmission=trans, dc_mwh=dc_prev, demand=D, avail0=avail0)


def design_trucks(site, opt, cand, shapes, avail0, elec_per_mwh_heat, p, carbon_price):
    far = cand[cand['road_m'] > 3000].sort_values('annual_mmbtu', ascending=False).head(opt['top_n'])
    D = set_demand(far, shapes)
    daily_d = D.reshape(365, 24).sum(axis=1)
    fleet_mwh_day = np.percentile(daily_d, 90)              # size fleet for a cold-ish day
    containers = int(np.ceil(fleet_mwh_day / (p['truck_container_mwh'] * p['truck_cycles_per_day'])))
    return dict(sel=far, pipe_m=0.0, loss_w=0, pipe_cost=0, kind='truck', transmission=False,
                containers=containers, fleet_mwh_day=fleet_mwh_day, demand=D, avail0=avail0)


# ======================================================================
# MONTE CARLO
# ======================================================================
def simulate(site, opt_name, des, b, shapes, sup, rng, n=N_MC):
    sel = des['sel']
    rows = []
    if sel.empty:
        return pd.DataFrame()
    types = sorted(sel['nrel_type'].unique())
    peak_kw_sel = np.array([peak_kw(r, shapes) for _, r in sel.iterrows()])
    # backup / peak boiler at the energy centre, sized like a real district system: it must carry
    # the worst-weather-year peak with the data center OFF (N-1), plus a 10% margin
    backup_mw = max(set_demand(sel, shapes, yr).max() for yr in range(10)) * 1.1
    kinds = ['ai_training', 'ai_inference', 'bitcoin_mining']
    n_runs = (sup['delivered_heat_mw'] if site == 'site1' else sup['phase1|ai_training|delivered_heat_mw']).shape[0]

    for k in range(n):
        p = {**sample(COMMON, rng), **sample(SITE_PARAMS[site], rng)}
        rate = p['discount_rate']
        a_pipe, a_eq, a_hp = annuity(rate, LIFE['pipe']), annuity(rate, LIFE['other']), annuity(rate, LIFE['hp'])
        yr = int(rng.integers(10))
        run = yr * (n_runs // 10) + int(rng.integers(n_runs // 10))
        kind = kinds[rng.integers(3)] if site == 'site2' else None
        s = supply_arrays(site, sup, kind, run)

        # demand of the connected set this draw (weather year + demand error + anchor size)
        mult = sel['group'].map({'measured': 1 + p['demand_err_measured'],
                                 'estimated': 1 + p['demand_err_estimated']}).fillna(1).to_numpy()
        if site == 'site2':
            grp = sel['group'].to_numpy()
            sp = SITE_PARAMS['site2']
            mult[grp == 'cornell'] = p['cornell_annual_mmbtu'] / np.mean(sp['cornell_annual_mmbtu'])
            mult[grp == 'greenhouse'] = (p['greenhouse_ha'] * p['greenhouse_kwh_m2']
                                         / (np.mean(sp['greenhouse_ha']) * np.mean(sp['greenhouse_kwh_m2'])))
        ann = sel['annual_mmbtu'].to_numpy() * mult
        dsh = sel['dhw_share'].to_numpy()
        S = {t: year_shape(shapes, t, 'space', yr) for t in types}
        Dh = {t: year_shape(shapes, t, 'dhw', yr) for t in types}
        tmask = {t: (sel['nrel_type'] == t).to_numpy() for t in types}
        D = np.zeros(8760)
        for t in types:
            m = tmask[t]
            D += (ann[m] * dsh[m]).sum() / 8760 * MW_PER_MMBTU_H * Dh[t] \
                + (ann[m] * (1 - dsh[m])).sum() / 8760 * MW_PER_MMBTU_H * S[t]

        if des['kind'] == 'pipe5':
            cop_b = p['building_hp_cop']
            avail = s['source'] * cop_b / (cop_b - 1)
            elec_ratio = np.full(8760, 1 / cop_b)
        elif des['kind'] == 'direct' and kind != 'bitcoin_mining':
            avail, elec_ratio = s['source'].copy(), np.zeros(8760)     # 45 C liquid loop used as-is
        else:
            avail = s['delivered'].copy()
            elec_ratio = np.divide(s['hp_elec'], s['delivered'], out=np.zeros(8760), where=s['delivered'] > 0)
        avail = np.maximum(avail - des['pipe_m'] * p['loss_w_per_m' if not des['transmission'] else
                                                     'transmission_loss_w_per_m'] * (des['loss_w'] > 0) / 1e6, 0)
        # pipe failures: each cuts deliverable DC heat by half for the repair time
        n_fail = rng.poisson(p['pipe_fail_per_km_yr'] * des['pipe_m'] / 1000)
        for _ in range(n_fail):
            t0 = int(rng.integers(8760))
            avail[t0:t0 + int(p['repair_hours'])] *= 0.5

        if des['kind'] == 'truck':
            # daily energy carried, limited by fleet, minus storage loss; spread over the day's demand
            day_avail = avail.reshape(365, 24).sum(axis=1)
            day_d = D.reshape(365, 24).sum(axis=1)
            fleet = des['containers'] * p['truck_container_mwh'] * p['truck_cycles_per_day']
            day_served = np.minimum.reduce([day_avail, day_d, np.full(365, fleet)]) * (1 - p['truck_loss'])
            frac = np.divide(day_served, day_d, out=np.zeros(365), where=day_d > 0)
            served = (D.reshape(365, 24) * frac[:, None]).ravel()
        else:
            served = np.minimum(avail, D)
        deficit = D - served
        unserved = np.maximum(deficit - backup_mw, 0)
        backup_heat = deficit - unserved

        # --- per-buyer economics (DC heat shared pro rata each hour)
        ratio = np.divide(served, D, out=np.zeros(8760), where=D > 0)
        dc_share = np.zeros(len(sel))
        for t in types:
            m = tmask[t]
            fd = (Dh[t] * ratio).sum() / max(Dh[t].sum(), 1e-9)
            fs = (S[t] * ratio).sum() / max(S[t].sum(), 1e-9)
            dc_share[m] = dsh[m] * fd + (1 - dsh[m]) * fs
        X = buyer_x(sel, site, p).to_numpy() * p['price_mult']
        ef = sel['ef'].to_numpy()
        retro = peak_kw_sel * np.where(sel['is_air'], p.get('retrofit_per_kw_air', 0),
                                       np.where(sel['is_old'], p['retrofit_per_kw_old'], 0))
        # Tariff: data-center heat is sold at (1 - discount) x the buyer's current cost. Backup / peak heat is
        # passed through at the buyer's own current cost and carbon, so it neither helps nor hurts anyone:
        # every dollar and tonne below is attributable to reused data-center heat.
        dc_heat = ann * dc_share                                         # MMBtu of DC heat each buyer receives
        ll97 = (dc_heat * ef * 268 * p['ll97_share_over_limit']) if site == 'site1' else 0
        buyer_net = p['buyer_discount'] * X * dc_heat + ll97 - retro * a_eq
        tariff_rev = ((1 - p['buyer_discount']) * X * dc_heat).sum()

        served_mwh, backup_mwh = served.sum(), backup_heat.sum()
        hp_elec = (served * elec_ratio).sum()
        pump = served.sum() * p['pump_frac']
        source_used = (served * (1 - elec_ratio)).sum()
        # capex
        hp_kw = served.max() * 1000
        if des['kind'] == 'pipe5':
            hp_capex = (peak_kw_sel * p['building_hp_per_kw']).sum()
            pipe_capex = des['pipe_m'] * p['pipe_per_m'] * 0.7
        elif des['kind'] == 'direct' and kind != 'bitcoin_mining':
            hp_capex = 0.0
            pipe_capex = des['pipe_m'] * p['pipe_per_m']
        else:
            hp_capex = hp_kw * p['central_hp_per_kw']
            pipe_capex = des['pipe_m'] * (p['transmission_per_m'] if des['transmission'] else p['pipe_per_m'])
        conn_capex = len(sel) * p['conn_fixed'] + (peak_kw_sel * p['conn_per_kw']).sum()
        boiler_capex = backup_mw * 1000 * p['backup_boiler_per_kw']
        truck_capex = des.get('containers', 0) * p.get('truck_container_capex', 0)
        truck_opex, truck_co2 = 0.0, 0.0
        if des['kind'] == 'truck':
            trips = served_mwh / p['truck_container_mwh']
            km = 2 * sel['road_m'].mean() * 1.4 / 1000
            truck_opex = trips * (km * p['truck_per_km'] + p['truck_handling'])
            truck_co2 = trips * km * p['truck_diesel_l_per_km'] * 2.68 / 1000
        capex_total = pipe_capex + hp_capex + conn_capex + boiler_capex + truck_capex
        op_cost = (pipe_capex * a_pipe + hp_capex * a_hp + (conn_capex + boiler_capex + truck_capex) * a_eq
                   + capex_total * p['om_frac'] + (hp_elec + pump) * p['elec_per_mwh'] + truck_opex)
        dc_payment = source_used * MMBTU_PER_MWH * p['dc_heat_price']
        operator_net = tariff_rev - op_cost - dc_payment

        if site == 'site1':
            cool = s['cooling_saved'] * np.divide(served, s['delivered'], out=np.zeros(8760), where=s['delivered'] > 0)
            dc_cool_value = cool.sum() * p['elec_per_mwh']
        else:
            dc_cool_value = source_used * p['cooling_kwh_saved_per_mwh'] / 1000 * p['elec_per_mwh']
        dc_capex = ((served * (1 - elec_ratio)).max() * 1000) * p['dc_tiein_per_kw']   # sized to heat exported
        dc_net = dc_payment + dc_cool_value - dc_capex * a_eq

        # --- carbon (t/yr): fuel displaced by DC heat - grid electricity, embodied carbon, trucks
        avoided = (dc_heat * ef).sum()
        backup_co2 = 0.0                                                 # backup = buyer's own fuel (unchanged)
        grid_co2 = (hp_elec + pump) * p['grid_t_per_mwh']
        embodied = (des['pipe_m'] * p['embodied_pipe_kg_per_m'] / LIFE['pipe']
                    + (peak_kw_sel.sum() if des['kind'] == 'pipe5' else hp_kw * (hp_capex > 0))
                    * p['embodied_hp_kg_per_kw'] / LIFE['hp']) / 1000
        net_co2 = avoided - backup_co2 - grid_co2 - embodied - truck_co2
        heat_mmbtu = (served_mwh + backup_mwh) * MMBTU_PER_MWH
        rows.append(dict(
            site=site, option=opt_name, draw=k, weather_year=2015 + yr, compute=kind or 'colo',
            warm=bool(unserved.sum() < 1e-6), unserved_mwh=unserved.sum(),
            dc_heat_share=served_mwh / max(served_mwh + backup_mwh, 1e-9),
            operator_net=operator_net, dc_net=dc_net, buyers_net=buyer_net.sum(),
            share_buyers_profit=(buyer_net > 0).mean(), all_buyers_profit=bool((buyer_net > 0).all()),
            all_profit=bool((buyer_net > 0).all() and operator_net > 0 and dc_net > 0),
            system_net=operator_net + dc_net + buyer_net.sum(),
            lcoh_usd_per_mmbtu=(op_cost + dc_payment) / max(served_mwh * MMBTU_PER_MWH, 1e-9),
            avg_tariff_usd_per_mmbtu=tariff_rev / max(dc_heat.sum(), 1e-9),
            capex_musd=capex_total / 1e6, net_co2_t=net_co2,
            abatement_cost_usd_per_t=-(operator_net + dc_net + buyer_net.sum()) / max(net_co2, 1e-9)))
    return pd.DataFrame(rows)


# ======================================================================
# MAIN
# ======================================================================
def steam_grade_check(p):
    """Con Ed steam (3rd-party network): could the DC sell into it? Needs ~180 C steam."""
    cop = 0.45 * (180 + 273.15) / (180 - 27)            # lift from 27 C to 180 C
    return cop, np.mean(p['elec_per_mwh']) / MMBTU_PER_MWH / cop


if __name__ == '__main__':
    pd.set_option('display.width', 220)
    rng = np.random.default_rng(7)
    summary, draws = [], []
    for site in ['site1', 'site2']:
        b, shapes, sup = load_site(site)
        if site == 'site2':
            b = add_site2_anchors(b, shapes, {**midpoint(COMMON), **midpoint(SITE_PARAMS['site2'])})
        print(f"\n=== {site}: {len(b):,} candidate users ===")
        if site == 'site1':
            cop, cost = steam_grade_check(SITE_PARAMS['site1'])
            print(f"Con Ed steam injection: heat pump to ~180 C steam has COP ~{cop:.1f} -> ~${cost:.0f}/MMBtu in "
                  f"electricity alone, before capex; plus steam-system interconnection is a Con Ed tariff matter. "
                  f"Kept as the incumbent/backup, not a transport route.")
        for opt_name, opt in OPTIONS[site].items():
            for obj, cprice in CARBON_PRICES.items():
                des = design(site, opt_name, b, shapes, sup, cprice)
                mc = simulate(site, opt_name, des, b, shapes, sup, rng)
                n_sel = len(des['sel'])
                if mc.empty:
                    print(f"  {opt_name:22s} [{obj}] no building worth connecting")
                    summary.append(dict(site=site, option=opt_name, objective=obj, description=opt['desc'],
                                        connected=0))
                    continue
                mc['objective'] = obj
                draws.append(mc)
                des['sel'].assign(objective=obj).to_csv(OUT / f'selected_{site}_{opt_name}_{obj}.csv', index=False)
                row = dict(
                    site=site, option=opt_name, objective=obj, description=opt['desc'], connected=n_sel,
                    pipe_km=des['pipe_m'] / 1000, heat_gwh=(des['sel']['annual_mmbtu'].sum() / MMBTU_PER_MWH / 1000),
                    dc_heat_share_p50=mc['dc_heat_share'].median(),
                    capex_musd_p50=mc['capex_musd'].median(),
                    lcoh_p50=mc['lcoh_usd_per_mmbtu'].median(),
                    tariff_p50=mc['avg_tariff_usd_per_mmbtu'].median(),
                    net_co2_t_p10=mc['net_co2_t'].quantile(0.1), net_co2_t_p50=mc['net_co2_t'].median(),
                    system_net_musd_p10=mc['system_net'].quantile(0.1) / 1e6,
                    system_net_musd_p50=mc['system_net'].median() / 1e6,
                    p_everyone_warm=mc['warm'].mean(),
                    p_operator_profit=(mc['operator_net'] > 0).mean(), p_dc_profit=(mc['dc_net'] > 0).mean(),
                    p_all_buyers_profit=mc['all_buyers_profit'].mean(),
                    p_every_party_profits=mc['all_profit'].mean(),
                    abatement_usd_per_t_p50=mc['abatement_cost_usd_per_t'].median())
                summary.append(row)
                print(f"  {opt_name:22s} [{obj:15s}] {n_sel:5d} users, {row['pipe_km']:5.1f} km, "
                      f"DC share {row['dc_heat_share_p50']:.0%}, cost ${row['lcoh_p50']:5.1f} vs tariff "
                      f"${row['tariff_p50']:5.1f}/MMBtu, CO2 -{row['net_co2_t_p50']:8,.0f} t/yr, "
                      f"system net ${row['system_net_musd_p50']:6.1f}M/yr | P(warm) {row['p_everyone_warm']:.0%} "
                      f"P(all profit) {row['p_every_party_profits']:.0%} "
                      f"[op {row['p_operator_profit']:.0%} dc {row['p_dc_profit']:.0%} buyers {row['p_all_buyers_profit']:.0%}]")
    pd.DataFrame(summary).to_csv(OUT / 'options_summary.csv', index=False)
    pd.concat(draws).to_csv(OUT / 'mc_draws.csv', index=False)
    print(f"\nWrote {OUT}")
