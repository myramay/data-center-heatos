"""
Hourly heat DEMAND and SUPPLY models for both hackathon sites.

    python heat_models.py            # trains, validates, writes outputs/models/

DEMAND (machine learning)
  Gradient-boosted quantile regression (P10/P50/P90) trained on NREL ComStock /
  ResStock hourly simulations (Manhattan + Tompkins County, 2018 actual weather).
  It learns the hourly SHAPE of space heating and of domestic hot water from
  outdoor temperature (now / 24 h / 72 h), hour, weekday, holidays, season and
  building type. Each building's annual heat (LL84 measured, or estimated) is then
  spread over the year with that shape, under 10 real weather years (2015-2024).
  Range = model quantiles x weather-year spread x uncertainty in the annual total.

SUPPLY (calibrated simulation + Monte Carlo)
  Site 1, 111 8th Ave: regression of the building's real monthly electricity on
  outdoor temperature separates flat IT load from cooling load; hourly IT load is
  then simulated for a colocation / carrier-hotel compute profile.
  Site 2, Lake Hawkeye: no operating data yet (proposed), so the 150 MW / 300 MW
  phases are simulated for three compute types (AI training, AI inference,
  bitcoin mining) with their own utilization, curtailment and cooling temperatures.
  Recovered heat is upgraded with a heat pump whose COP follows source and sink
  temperature hour by hour.

Every assumption that is not from a dataset is in the ASSUMPTIONS blocks.
"""
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score

ROOT = Path(__file__).parent
DATA = ROOT / 'heat-reuse-data' / 'data'
OUT = ROOT / 'outputs' / 'models'
OUT.mkdir(parents=True, exist_ok=True)
NREL = DATA / 'nrel_load_profiles'
RNG = np.random.default_rng(42)

WEATHER_YEARS = list(range(2015, 2025))     # 10 real years -> weather range
QUANTILES = [0.1, 0.5, 0.9]
KW_PER_MMBTU_H = 1e6 / 3412                  # 1 MMBtu/h = 293 kW

SITES = {
    'site1': dict(name='111 8th Ave (Manhattan)', station='nyc_central_park',
                  lat=40.740704, lon=-74.001844, radius_m=2200,
                  rings_m=[250, 500, 1000, 1500, 2200]),
    'site2': dict(name='Lake Hawkeye (Lansing)', station='ithaca_airport',
                  lat=None, lon=None, radius_m=15000,          # rural: reaches Lansing centre, Ithaca, Cornell
                  rings_m=[1000, 2200, 5000, 10000, 15000]),
}

# ---------------- DEMAND ASSUMPTIONS ----------------
ANNUAL_UNCERTAINTY = {'measured': 0.10, 'estimated': 0.40}   # +/- on a building's annual heat
BOILER_EFF = 0.80
RES_UNIT_FT2 = {'single-family_detached': 2000, 'single-family_attached': 1500,
                'multi-family_with_2_-_4_units': 1000, 'multi-family_with_5plus_units': 850,
                'mobile_home': 1100}
# Site 2 buyer prices: NYS SEDS residential 2021 $/MMBtu (fuel), appliance efficiency, kgCO2/MMBtu (EPA)
FUEL_INFO = {'gas': (13.35, 0.80, 53.1), 'oil': (18.59, 0.80, 74.0), 'propane': (31.74, 0.80, 62.9),
             'electric': (57.10, 1.00, 0.0), 'wood': (6.77, 0.60, 0.0), 'unknown': (np.nan, 0.80, np.nan)}

# ---------------- SUPPLY ASSUMPTIONS ----------------
N_DRAWS_PER_YEAR = 20               # Monte Carlo draws per weather year (x10 years = 200 realizations)
CARNOT_EFF = 0.45                   # real heat pump COP = 0.45 x Carnot COP
COP_MAX = 7.0
SINK_C = {'cold': (-10, 70), 'warm': (15, 55)}   # outdoor-reset supply temp: 70 C at -10 C -> 55 C at >=15 C (DHW floor)
SITE1 = dict(site_id=7536925, dc_ft2=530_990, gfa_ft2=2_375_706,
             non_dc_kwh_ft2=(10, 16),          # office/other electricity intensity range
             capture=(0.70, 0.90),             # share of IT heat captured in a water loop
             source_c=27,                      # air-cooled CRAH / rear-door loop return temperature
             level_sd=0.04)                    # tenant churn: year-level drift of IT load
# Google power traces (cluster-data/powerdata_2019): real 5-min power of 57 data-center power domains.
# Cell locations are hidden, so each cell is aligned on its own daily low, placed at this local hour.
GOOGLE_TRACES = DATA / 'cluster-data' / 'powerdata_2019'
TROUGH_LOCAL_HOUR = 5               # internet / compute demand is lowest around 05:00 local
# workload mix ("production" = user-facing share of power) used for each compute type, as a
# percentile of the 57 Google power domains: user-facing serving sits high, mixed colo in the middle
WORKLOAD_MIX_PCTILE = {'site1_colo': 50, 'ai_inference': 90}
SITE2_PHASES_MW = {'phase1': 150, 'phase2': 300}   # TeraWulf planning board deck, Apr 2026
SITE2_PUE = 1.20                                   # liquid cooling + dry/adiabatic coolers
SITE2_COMPUTE = {
    # utilization pattern, heat captured to liquid, source temperature
    'ai_training':  dict(capture=0.75, source_c=45),   # direct-to-chip, ~45 C return
    'ai_inference': dict(capture=0.75, source_c=45),
    'bitcoin_mining': dict(capture=0.60, source_c=32), # air-cooled miners (TeraWulf's existing practice)
}


def section(t):
    print('\n' + '=' * 78 + '\n' + t + '\n' + '=' * 78)


# ======================================================================
# WEATHER
# ======================================================================
def load_weather(station, years):
    """Hourly dry-bulb temperature (C) in local STANDARD time (matches NREL), no Feb 29."""
    out = []
    for y in years:
        w = pd.read_csv(DATA / 'weather' / station / f'{y}.csv', usecols=['DATE', 'TMP'], low_memory=False)
        val = pd.to_numeric(w['TMP'].str.split(',').str[0], errors='coerce') / 10
        t = pd.Series(val.values, index=pd.to_datetime(w['DATE']) - pd.Timedelta(hours=5))
        t = t[(t > -45) & (t < 50)]
        out.append(t.resample('h').mean())
    s = pd.concat(out)
    full = pd.date_range(f'{years[0]}-01-01', f'{years[-1]}-12-31 23:00', freq='h')
    s = s.reindex(full).interpolate(limit=12).ffill().bfill()
    return s[~((s.index.month == 2) & (s.index.day == 29))]


HOLIDAYS = set(USFederalHolidayCalendar().holidays('2010-01-01', '2030-12-31').date)


def features(temp):
    """Model inputs for each hour of a temperature series."""
    idx = temp.index
    return pd.DataFrame({
        'temp': temp.values,
        'temp_24h': temp.rolling(24, min_periods=1).mean().values,
        'temp_72h': temp.rolling(72, min_periods=1).mean().values,
        'hdh': np.clip(18 - temp.values, 0, None),
        'hour': idx.hour, 'dow': idx.dayofweek,
        'holiday': np.isin(idx.date, list(HOLIDAYS)).astype(int),
        'season': np.cos(2 * np.pi * (idx.dayofyear - 172) / 365),   # +1 summer, -1 winter (daylight, mains water temp)
    }, index=idx)


# ======================================================================
# DEMAND MODEL
# ======================================================================
def load_nrel_training():
    """Hourly space-heating and hot-water series per (region, building type), normalized to mean 1."""
    rows = []
    blend = None
    for county, station in [('g3600610', 'nyc_central_park'), ('g3601090', 'ithaca_airport')]:
        temp = load_weather(station, [2018])
        for f in sorted((NREL / 'comstock_amy2018_release_3').glob(f'up0-{county}-*.csv')):
            btype = f.stem.split('-')[-1]
            hdr = pd.read_csv(f, nrows=0).columns
            sh_cols = [c for c in hdr if c.endswith('.heating.energy_consumption.kwh')]
            wh_cols = [c for c in hdr if c.endswith('.water_systems.energy_consumption.kwh')]
            d = pd.read_csv(f, usecols=['timestamp', 'floor_area_represented', *sh_cols, *wh_cols])
            ts = pd.to_datetime(d['timestamp']) - pd.Timedelta(minutes=15)      # period-ending -> starting
            d = d.set_index(ts.dt.floor('h'))
            space, dhw = d[sh_cols].sum(axis=1).groupby(level=0).sum(), d[wh_cols].sum(axis=1).groupby(level=0).sum()
            area = d['floor_area_represented'].iloc[0]
            rows.append(dict(region=county, btype=btype, temp=temp, space=space, dhw=dhw, area=area,
                             useful_kbtu_ft2_space=space.sum() * 3.412 * BOILER_EFF / area,
                             useful_kbtu_ft2_dhw=dhw.sum() * 3.412 * BOILER_EFF / area))
    # ResStock is a NY-STATE aggregate: approximate its weather with a NYC/upstate blend
    blend = 0.6 * load_weather('nyc_central_park', [2018]) + 0.4 * load_weather('ithaca_airport', [2018])
    for f in sorted((NREL / 'resstock_amy2018_release_1').glob('up00-ny-*.csv')):
        btype = f.stem.replace('up00-ny-', '')
        d = pd.read_csv(f, usecols=['timestamp', 'units_represented', 'out.load.heating.energy_delivered..kbtu',
                                    'out.load.hot_water.energy_delivered..kbtu'])
        ts = pd.to_datetime(d['timestamp']) - pd.Timedelta(minutes=15)
        d = d.set_index(ts.dt.floor('h'))
        space = d['out.load.heating.energy_delivered..kbtu'].groupby(level=0).sum()
        dhw = d['out.load.hot_water.energy_delivered..kbtu'].groupby(level=0).sum()
        units = d['units_represented'].iloc[0]
        rows.append(dict(region='ny_state', btype=btype, temp=blend, space=space, dhw=dhw, units=units,
                         useful_kbtu_unit_space=space.sum() / units, useful_kbtu_unit_dhw=dhw.sum() / units))
    return rows, blend


def training_frame(series_rows):
    """
    Target = hourly load / (NYC mean load of the same building type, per ft2 or per unit).
    Using ONE reference per type (not each series' own mean) keeps the target physical:
    a colder county really does sit above 1 on average, so temperature response transfers.
    """
    def per_size(r, comp):
        size = r.get('area') or r.get('units')
        return r[comp] / size
    ref = {}
    for r in series_rows:
        if r['region'] in ('g3600610', 'ny_state'):
            for comp in ['space', 'dhw']:
                ref[(r['btype'], comp)] = per_size(r, comp).mean()
    frames = []
    for r in series_rows:
        X = features(r['temp'])
        for comp in ['space', 'dhw']:
            y = per_size(r, comp).reindex(X.index).fillna(0)
            if ref.get((r['btype'], comp), 0) <= 0:
                continue
            frames.append(X.assign(btype=r['btype'], region=r['region'], component=comp,
                                   y=(y / ref[(r['btype'], comp)]).values))
    return pd.concat(frames, ignore_index=True)


class DemandModel:
    """One quantile gradient-boosting model per (component, quantile); building type is a categorical input."""
    FEATS = ['temp', 'temp_24h', 'temp_72h', 'hdh', 'hour', 'dow', 'holiday', 'season', 'btype_code']

    def __init__(self, df):
        self.types = sorted(df['btype'].unique())
        self.code = {t: i for i, t in enumerate(self.types)}
        self.models = {}
        self.widen = {'space': 0.0, 'dhw': 0.0}     # conformal widening, set from held-out data

    def _X(self, df):
        return df.assign(btype_code=df['btype'].map(self.code))[self.FEATS]

    def fit(self, df):
        for comp in ['space', 'dhw']:
            d = df[df['component'] == comp]
            for q in QUANTILES:
                m = HistGradientBoostingRegressor(loss='quantile', quantile=q, max_iter=300, learning_rate=0.08,
                                                  max_leaf_nodes=48, categorical_features=[8], random_state=0)
                self.models[(comp, q)] = m.fit(self._X(d), d['y'])
        return self

    def _raw(self, X, comp):
        return {q: self.models[(comp, q)].predict(X) for q in QUANTILES}

    def _calibrated(self, p, comp):
        """Conformalized quantile regression: widen P10/P90 by a share of P50 learned on held-out days."""
        w = self.widen[comp] * np.maximum(p[0.5], 0)
        return {0.1: p[0.1] - w, 0.5: p[0.5], 0.9: p[0.9] + w}

    def predict(self, X, btype, comp):
        Xb = X.assign(btype=btype)
        p = self._calibrated(self._raw(self._X(Xb), comp), comp)
        return {q: np.clip(v, 0, None) for q, v in p.items()}


def validate(df):
    """
    (1) hold out 20% of days in NYC + NY-state series -> accuracy, and calibrate the P10-P90 band to 80%
    (2) hold out ALL Tompkins County series (colder climate, never seen) -> honest test of transfer + band
    """
    lines = []
    test_climate = df['region'] == 'g3601090'
    doy = df.groupby(['region', 'btype', 'component']).cumcount() // 24
    held_days = set(np.random.default_rng(0).choice(365, 73, replace=False))
    test_days = ~test_climate & doy.isin(held_days)
    model = DemandModel(df).fit(df[~test_climate & ~test_days])
    widen = {}
    for comp in ['space', 'dhw']:
        d = df[test_days & (df['component'] == comp)]
        p = model._raw(model._X(d), comp)
        excess = np.maximum(p[0.1] - d['y'], d['y'] - p[0.9]) / np.maximum(p[0.5], 0.05)
        widen[comp] = max(float(np.quantile(excess, 0.80)), 0.0)
    model.widen = widen
    for name, mask in [('Held-out 20% of days (NYC + NY state)', test_days),
                       ('Unseen colder climate: Tompkins ComStock', test_climate)]:
        for comp in ['space', 'dhw']:
            d = df[mask & (df['component'] == comp)]
            p = model._calibrated(model._raw(model._X(d), comp), comp)
            inside = ((d['y'] >= p[0.1]) & (d['y'] <= p[0.9])).mean()
            grp = np.arange(len(d)) // 24
            daily_r2 = r2_score(d['y'].groupby(grp).mean(), pd.Series(p[0.5]).groupby(grp).mean())
            lines.append(f"{name:42s} {comp:5s}  hourly R2={r2_score(d['y'], p[0.5]):.3f}  daily R2={daily_r2:.3f}  "
                         f"MAE={mean_absolute_error(d['y'], p[0.5]):.3f}  P10-P90 coverage={inside:.0%}")
    lines.append(f"Conformal widening (share of P50 added to each side): "
                 f"space {widen['space']:.2f}, dhw {widen['dhw']:.2f}")
    return lines, widen


def typical_shapes(model, station, btypes):
    """
    For each building type: hourly shape arrays (years x 8760) for each component and quantile,
    scaled so the multi-year P50 averages 1. A cold year therefore sums to more than a mild one.
    """
    temp = load_weather(station, WEATHER_YEARS)
    X = features(temp)
    shapes = {}
    for bt in btypes:
        for comp in ['space', 'dhw']:
            p = model.predict(X, bt, comp)
            norm = p[0.5].mean()
            shapes[(bt, comp)] = {q: (p[q] / norm).reshape(len(WEATHER_YEARS), 8760) for q in QUANTILES}
    return shapes, temp


def nrel_type_site1(row):
    c, g, u = row['category'], row['gfa_ft2'] or 0, str(row['use_type']).lower()
    if c == 'Residential':
        return 'single-family_attached' if str(row['pluto_class'])[:1] in 'AB' else 'multi-family_with_5plus_units'
    if c == 'Office' or c == 'Data center':
        return 'largeoffice' if g > 150_000 else 'mediumoffice' if g > 25_000 else 'smalloffice'
    if c == 'Hotel':
        return 'largehotel' if g > 100_000 else 'smallhotel'
    if c == 'Education':
        return 'secondaryschool' if g > 100_000 else 'primaryschool'
    if c == 'Healthcare':
        return 'hospital' if 'hospital' in u else 'outpatient'
    if 'restaurant' in u or 'food' in u:
        return 'fullservicerestaurant'
    if 'warehouse' in u or 'storage' in u:
        return 'warehouse'
    return 'retailstandalone'


def site1_buildings():
    b = pd.read_csv(ROOT / 'outputs' / 'site1_offtakers_2200m.csv', low_memory=False)
    b = b[b['useful_heat_mmbtu'] > 0].copy()
    b['nrel_type'] = b.apply(nrel_type_site1, axis=1)
    b['annual_mmbtu'] = b['useful_heat_mmbtu']
    b['dhw_share'] = b['base_load_share_filled'].clip(0.02, 0.95)
    b['uncertainty'] = np.where(b['record_type'] == 'LL84 measured',
                                ANNUAL_UNCERTAINTY['measured'], ANNUAL_UNCERTAINTY['estimated'])
    return b


def site2_buildings(nrel_rows, blend_temp):
    """Tompkins parcels around Lake Hawkeye -> one row per building with estimated annual heat."""
    p = gpd.read_file(DATA / 'site2_lansing' / 'tompkins_parcels.geojson').to_crs(32618)
    site = p[p['PRIMARY_OWNER'].str.contains('Cayuga Operating', case=False, na=False)]
    geom = site.geometry
    centre = (geom.union_all() if hasattr(geom, 'union_all') else geom.unary_union).centroid
    lonlat = gpd.GeoSeries([centre], crs=32618).to_crs(4326).iloc[0]
    SITES['site2'].update(lat=lonlat.y, lon=lonlat.x)
    p['dist_m'] = p.geometry.centroid.distance(centre)
    p = p[(p['dist_m'] <= SITES['site2']['radius_m']) & ~p.index.isin(site.index)].copy()

    cls = pd.to_numeric(p['PROP_CLASS'], errors='coerce').fillna(0).astype(int)
    def type_of(c):
        if c in (210, 215, 240, 241, 242, 250): return 'single-family_detached', 'Residential'
        if c in (220, 230, 280, 281): return 'multi-family_with_2_-_4_units', 'Residential'
        if c == 270: return 'mobile_home', 'Residential'
        if c == 411: return 'multi-family_with_5plus_units', 'Residential'
        if c in (612, 613): return 'primaryschool', 'Education'
        if c in (641, 642): return 'outpatient', 'Healthcare'
        if c in (414, 415, 418): return 'smallhotel', 'Hotel'
        if 420 <= c < 430: return 'fullservicerestaurant', 'Other'
        if 440 <= c < 450 or 700 <= c < 800: return 'warehouse', 'Other'
        if 450 <= c < 460: return 'retailstandalone', 'Other'
        if 460 <= c < 470: return 'smalloffice', 'Office'
        if 600 <= c < 700 or 500 <= c < 600 or 480 <= c < 490: return 'smalloffice', 'Community/Other'
        if 100 <= c < 200: return 'single-family_detached', 'Farm residence'
        return None, None
    tc = cls.map(type_of)
    p['nrel_type'], p['category'] = tc.str[0], tc.str[1]
    p['res_ft2'] = pd.to_numeric(p['SQFT_LIVING'], errors='coerce')
    p['nonres_ft2'] = pd.to_numeric(p['GFA'], errors='coerce')
    is_res = p['nrel_type'].isin(list(RES_UNIT_FT2))
    p['floor_ft2'] = np.where(is_res, p['res_ft2'], p['nonres_ft2'].fillna(p['res_ft2']))
    no_area = p['nrel_type'].notna() & ~(p['floor_ft2'] > 0)
    print(f"Site 2 parcels within {SITES['site2']['radius_m']} m: {len(p):,}; usable buildings "
          f"{(p['nrel_type'].notna() & ~no_area).sum():,}; skipped (no floor area) {no_area.sum():,}; "
          f"seasonal / vacant / utility parcels skipped {p['nrel_type'].isna().sum():,}")
    p = p[p['nrel_type'].notna() & ~no_area].copy()

    # annual useful heat per ft2: ResStock (per unit -> per ft2, climate-adjusted) or ComStock Tompkins
    ith = load_weather('ithaca_airport', [2018])
    hdd_ratio = np.clip(18 - ith, 0, None).sum() / np.clip(18 - blend_temp, 0, None).sum()
    space_i, dhw_i = {}, {}
    for r in nrel_rows:
        if r['region'] == 'ny_state':
            ft2 = RES_UNIT_FT2[r['btype']]
            space_i[r['btype']] = r['useful_kbtu_unit_space'] / ft2 * hdd_ratio
            dhw_i[r['btype']] = r['useful_kbtu_unit_dhw'] / ft2
        elif r['region'] == 'g3601090':
            space_i[r['btype']], dhw_i[r['btype']] = r['useful_kbtu_ft2_space'], r['useful_kbtu_ft2_dhw']
    space_i.setdefault('outpatient', next(r['useful_kbtu_ft2_space'] for r in nrel_rows if r['btype'] == 'outpatient'))
    dhw_i.setdefault('outpatient', next(r['useful_kbtu_ft2_dhw'] for r in nrel_rows if r['btype'] == 'outpatient'))
    p['annual_mmbtu'] = p['floor_ft2'] * (p['nrel_type'].map(space_i) + p['nrel_type'].map(dhw_i)) / 1000
    p['dhw_share'] = p['nrel_type'].map(dhw_i) / (p['nrel_type'].map(space_i) + p['nrel_type'].map(dhw_i))
    p['uncertainty'] = ANNUAL_UNCERTAINTY['estimated']

    fuel = p['FUEL_TYPE_DESC'].fillna('').str.lower()
    p['main_fuel'] = np.select([fuel.str.contains('gas'), fuel.str.contains('oil'), fuel.str.contains('propane|lpg'),
                                fuel.str.contains('electric'), fuel.str.contains('wood')],
                               ['gas', 'oil', 'propane', 'electric', 'wood'], 'unknown')
    price, eff, kg = (p['main_fuel'].map({k: v[i] for k, v in FUEL_INFO.items()}) for i in range(3))
    p['current_cost_X_usd_per_mmbtu'] = price / eff
    p['thermal_co2_t'] = p['annual_mmbtu'] / eff * kg / 1000
    p['id'] = p['SWIS_PRINT_KEY_ID']
    p['name'], p['address'] = p['PRIMARY_OWNER'], p['PARCEL_ADDR']
    p['year_built'] = p['YR_BLT']
    pts = p.geometry.centroid.to_crs(4326)
    p['lat'], p['lon'] = pts.y, pts.x
    keep = ['id', 'name', 'address', 'lat', 'lon', 'dist_m', 'PROP_CLASS', 'category', 'nrel_type', 'floor_ft2',
            'year_built', 'HEAT_TYPE_DESC', 'main_fuel', 'annual_mmbtu', 'dhw_share', 'uncertainty',
            'current_cost_X_usd_per_mmbtu', 'thermal_co2_t']
    return pd.DataFrame(p[keep])


def building_hourly_kw(b_row, shapes):
    """Hourly heat need of ONE building over a typical year: dict of P10/P50/P90 arrays in kW (8760)."""
    s = shapes[(b_row['nrel_type'], 'space')]
    d = shapes[(b_row['nrel_type'], 'dhw')]
    lo_hi = {0.1: 1 - b_row['uncertainty'], 0.5: 1.0, 0.9: 1 + b_row['uncertainty']}
    yr_pct = {0.1: 10, 0.5: 50, 0.9: 90}
    out = {}
    for q in QUANTILES:
        shape = b_row['dhw_share'] * d[q] + (1 - b_row['dhw_share']) * s[q]      # years x 8760
        out[q] = np.percentile(shape, yr_pct[q], axis=0) * b_row['annual_mmbtu'] * lo_hi[q] / 8760 * KW_PER_MMBTU_H
    return out


def aggregate_demand(b, shapes, rings):
    """Sum of all buildings per ring, per weather year (so the range keeps real weather correlation)."""
    res = {}
    for ring in rings:
        sub = b[b['dist_m'] <= ring]
        tot = {q: np.zeros((len(WEATHER_YEARS), 8760)) for q in QUANTILES}
        lo_hi = {0.1: 1 - sub['uncertainty'], 0.5: 1.0 + 0 * sub['uncertainty'], 0.9: 1 + sub['uncertainty']}
        for bt, g in sub.groupby('nrel_type'):
            for q in QUANTILES:
                a_dhw = (g['annual_mmbtu'] * g['dhw_share'] * lo_hi[q].loc[g.index]).sum()
                a_sp = (g['annual_mmbtu'] * (1 - g['dhw_share']) * lo_hi[q].loc[g.index]).sum()
                tot[q] += (a_dhw * shapes[(bt, 'dhw')][q] + a_sp * shapes[(bt, 'space')][q]) / 8760
        mw = {q: tot[q] * KW_PER_MMBTU_H / 1000 for q in QUANTILES}
        res[ring] = {'P10': np.percentile(mw[0.1], 10, axis=0), 'P50': np.median(mw[0.5], axis=0),
                     'P90': np.percentile(mw[0.9], 90, axis=0), 'by_year_P50': mw[0.5]}
    return res


def building_summaries(b, shapes):
    """Per-building hourly statistics for every building (computed in chunks; full matrices are too big to save)."""
    stats = []
    summer = np.r_[151 * 24:243 * 24]        # Jun-Aug
    for bt, g in b.groupby('nrel_type'):
        for q, tag in [(0.5, 'p50'), (0.9, 'p90'), (0.1, 'p10')]:
            pct = {0.1: 10, 0.5: 50, 0.9: 90}[q]
            S = np.percentile(shapes[(bt, 'space')][q], pct, axis=0)
            D = np.percentile(shapes[(bt, 'dhw')][q], pct, axis=0)
            mult = {0.1: 1 - g['uncertainty'], 0.5: 1.0, 0.9: 1 + g['uncertainty']}[q]
            scale = (g['annual_mmbtu'] * mult / 8760 * KW_PER_MMBTU_H).to_numpy()
            dsh = g['dhw_share'].to_numpy()
            for i0 in range(0, len(g), 1000):
                sl = slice(i0, i0 + 1000)
                M = (dsh[sl, None] * D + (1 - dsh[sl, None]) * S) * scale[sl, None]
                stats.append(pd.DataFrame({
                    'row': g.index[sl], f'peak_kw_{tag}': M.max(axis=1), f'mean_kw_{tag}': M.mean(axis=1),
                    f'summer_min_kw_{tag}': M[:, summer].min(axis=1),
                    f'hours_above_half_peak_{tag}': (M > 0.5 * M.max(axis=1, keepdims=True)).sum(axis=1)}))
    s = pd.concat(stats).groupby('row').first()
    return b.join(s)


# ======================================================================
# SUPPLY MODEL
# ======================================================================
def heat_pump(source_c, outdoor_c):
    """Hourly COP from source temperature and outdoor-reset sink temperature."""
    (t_cold, s_cold), (t_warm, s_warm) = SINK_C['cold'], SINK_C['warm']
    sink = np.interp(outdoor_c, [t_cold, t_warm], [s_cold, s_warm])
    cop = CARNOT_EFF * (sink + 273.15) / np.maximum(sink - source_c, 5)
    return np.clip(cop, 1.5, COP_MAX), sink


def ar1_noise(n, sd, rho=0.9):
    e = RNG.normal(0, sd * np.sqrt(1 - rho ** 2), n)
    x = np.empty(n)
    x[0] = RNG.normal(0, sd)
    for i in range(1, n):
        x[i] = rho * x[i - 1] + e[i]
    return x


class ComputeLoadModel:
    """
    ML model of how a data center's IT power moves hour to hour, learned from Google's 2019 power
    traces. Target = hourly power / that power domain's monthly mean. Inputs = hours after the
    cell's daily low, day of week, workload mix (production share) and power-plane design.
    Realistic noise comes from bootstrapping real residuals in one-week blocks.
    """
    FEATS = ['hrs_after_trough', 'dow', 'production_share', 'is_mvpp']

    def __init__(self):
        frames = []
        for f in sorted(GOOGLE_TRACES.glob('*.csv.gz')):
            d = pd.read_csv(f)
            d = d[~d['bad_measurement_data'].astype(bool) & ~d['bad_production_power_data'].astype(bool)]
            hour = ((d['time'] / 1e6 - 600) // 3600).astype(int)          # hours since 2019-05-01 00:00 PT
            h = d.groupby(hour)[['measured_power_util', 'production_power_util']].mean()
            h = h.reindex(range(31 * 24)).interpolate(limit=6).dropna()
            frames.append(h.assign(pdu=f.name.split('.')[0], cell=f.name[4]))
        g = pd.concat(frames).rename_axis('hour').reset_index()
        g['rel'] = g['measured_power_util'] / g.groupby('pdu')['measured_power_util'].transform('mean')
        g['production_share'] = (g.groupby('pdu')['production_power_util'].transform('mean')
                                 / g.groupby('pdu')['measured_power_util'].transform('mean'))
        g['is_mvpp'] = g['pdu'].str.contains('mvpp').astype(int)
        # align each cell on its own daily low (true time zone unknown)
        prof = g.groupby(['cell', g['hour'] % 24])['rel'].mean()
        trough = prof.groupby(level=0).idxmin().map(lambda t: t[1])
        g['hrs_after_trough'] = (g['hour'] % 24 - g['cell'].map(trough)) % 24
        g['dow'] = (g['hour'] // 24 + 2) % 7                               # 2019-05-01 was a Wednesday
        self.data = g
        self.share_by_pdu = g.groupby('pdu')['production_share'].first()

    def fit(self, rows=None):
        d = self.data if rows is None else self.data[rows]
        self.models = {q: HistGradientBoostingRegressor(loss='quantile', quantile=q, max_iter=200,
                                                        learning_rate=0.05, random_state=0)
                       .fit(d[self.FEATS], d['rel']) for q in QUANTILES}
        return self

    def validate(self):
        """Leave-one-cell-out: train on 9 clusters, predict the 10th."""
        lines, scores, cover = [], [], []
        for cell in sorted(self.data['cell'].unique()):
            test = self.data['cell'] == cell
            self.fit(~test)
            d = self.data[test]
            p = {q: m.predict(d[self.FEATS]) for q, m in self.models.items()}
            scores.append(mean_absolute_error(d['rel'], p[0.5]))
            cover.append(((d['rel'] >= p[0.1]) & (d['rel'] <= p[0.9])).mean())
        self.fit()
        resid = self.data['rel'] - self.models[0.5].predict(self.data[self.FEATS])
        self.data['resid'] = resid
        # residual series for bootstrapping: only domains with at least two weeks of clean hours
        self.resid_series = [r.to_numpy() for _, r in self.data.groupby('pdu')['resid'] if r.notna().sum() >= 336]
        lines.append(f"Google trace model, leave-one-cluster-out over {self.data['cell'].nunique()} clusters: "
                     f"MAE {np.mean(scores):.3f} of mean load, P10-P90 coverage {np.mean(cover):.0%}")
        return lines

    def describe(self):
        g = self.data
        daily = g.groupby(['pdu', g['hour'] // 24])['rel'].agg(['max', 'min'])
        ramp = g.groupby('pdu')['rel'].diff().abs()
        return (f"Real data-center power (57 domains): hourly load P10-P90 = {np.percentile(g['rel'], 10):.2f}-"
                f"{np.percentile(g['rel'], 90):.2f} x mean, typical daily swing "
                f"{(daily['max'] - daily['min']).median():.1%}, 99th-pct hour-to-hour ramp {ramp.quantile(0.99):.1%}; "
                f"production share {self.share_by_pdu.min():.2f}-{self.share_by_pdu.max():.2f} across domains")

    def simulate(self, idx, production_share, rng=RNG):
        """Relative IT load (mean ~1) for every hour in idx: P50 shape + a bootstrapped week of real residuals."""
        X = pd.DataFrame({'hrs_after_trough': (idx.hour - TROUGH_LOCAL_HOUR) % 24, 'dow': idx.dayofweek,
                          'production_share': production_share, 'is_mvpp': 0})
        shape = self.models[0.5].predict(X[self.FEATS])
        res = np.empty(len(idx))
        for s in range(0, len(idx), 168):
            r = self.resid_series[rng.integers(len(self.resid_series))]
            start = rng.integers(0, len(r) - 168 + 1)
            res[s:s + 168] = r[start:start + 168][:len(idx) - s]
        return shape + res

    def share_for(self, kind):
        return float(np.percentile(self.share_by_pdu, WORKLOAD_MIX_PCTILE[kind]))


def fit_site1_electricity():
    """Monthly regression: average MW = level(year) + b * mean(max(T - Tbal, 0)). Separates IT from cooling."""
    m = pd.read_csv(DATA / 'site1_nyc' / 'll84_monthly.csv', low_memory=False,
                    usecols=['Calendar Year', 'Month', 'Property Id', 'Electricity Use  (kBtu)',
                             'Electricity Use (Grid) - Monthly (kBtu)'])
    m = m[m['Property Id'] == SITE1['site_id']].copy()
    m['kbtu'] = pd.to_numeric(m['Electricity Use (Grid) - Monthly (kBtu)'], errors='coerce').fillna(
        pd.to_numeric(m['Electricity Use  (kBtu)'], errors='coerce'))
    m['date'] = pd.to_datetime(m['Month'], format='%y-%b')
    m = m.dropna(subset=['kbtu']).drop_duplicates('date')
    m['mw'] = m['kbtu'] / 3412.14 / (m['date'].dt.days_in_month * 24)
    temp = load_weather('nyc_central_park', sorted(m['date'].dt.year.unique()))
    best = None
    for tbal in np.arange(8, 22, 0.5):
        cdh = np.clip(temp - tbal, 0, None).resample('MS').mean()
        x = cdh.reindex(m['date']).to_numpy()
        yrs = pd.get_dummies(m['date'].dt.year).to_numpy(float)
        A = np.column_stack([yrs, x])
        coef, *_ = np.linalg.lstsq(A, m['mw'], rcond=None)
        pred = A @ coef
        r2 = r2_score(m['mw'], pred)
        if best is None or r2 > best['r2']:
            resid = m['mw'] - pred
            se_b = np.sqrt(resid.var(ddof=A.shape[1]) * np.linalg.inv(A.T @ A)[-1, -1])
            best = dict(tbal=tbal, b=coef[-1], se_b=se_b, r2=r2,
                        level=dict(zip(sorted(m['date'].dt.year.unique()), coef[:-1])), n=len(m))
    return best


def office_shape():
    d = pd.read_csv(NREL / 'comstock_amy2018_release_3' / 'up0-g3600610-largeoffice.csv',
                    usecols=['timestamp', 'out.electricity.interior_equipment.energy_consumption.kwh',
                             'out.electricity.interior_lighting.energy_consumption.kwh'])
    ts = (pd.to_datetime(d['timestamp']) - pd.Timedelta(minutes=15)).dt.floor('h')
    s = d.iloc[:, 1:].sum(axis=1).groupby(ts.values).sum()
    prof = s.groupby([s.index.dayofweek >= 5, s.index.hour]).mean()
    return prof / s.mean()


def simulate_site1_supply(fit, temp, compute):
    """200 hourly realizations (10 weather years x 20 draws) of 111 8th Ave heat supply."""
    share = compute.share_for('site1_colo')
    off = office_shape()
    level = fit['level'][max(fit['level'])]          # latest year's non-cooling MW
    years = temp.index.year.unique()
    out = {k: [] for k in ['total_mw', 'it_mw', 'cooling_mw', 'source_heat_mw', 'delivered_heat_mw',
                           'hp_elec_mw', 'cop', 'cooling_saved_mw']}
    for y in years:
        t = temp[temp.index.year == y]
        idx = t.index
        office_prof = off.loc[list(zip(idx.dayofweek >= 5, idx.hour))].to_numpy()
        for _ in range(N_DRAWS_PER_YEAR):
            non_dc_mw = (SITE1['gfa_ft2'] - SITE1['dc_ft2']) * RNG.uniform(*SITE1['non_dc_kwh_ft2']) / 8760 / 1000
            it_mean = (level - non_dc_mw) * (1 + RNG.normal(0, SITE1['level_sd']))
            it = it_mean * compute.simulate(idx, share)                 # learned from Google power traces
            b = max(RNG.normal(fit['b'], fit['se_b']), 0)
            cooling = b * np.clip(t.to_numpy() - fit['tbal'], 0, None)
            capture = RNG.uniform(*SITE1['capture'])
            src = capture * it
            cop, _ = heat_pump(SITE1['source_c'], t.to_numpy())
            delivered = src * cop / (cop - 1)
            for k, v in [('total_mw', it + cooling + non_dc_mw * office_prof), ('it_mw', it),
                         ('cooling_mw', cooling), ('source_heat_mw', src), ('delivered_heat_mw', delivered),
                         ('hp_elec_mw', delivered / cop), ('cop', cop), ('cooling_saved_mw', capture * cooling)]:
                out[k].append(np.asarray(v))
    return {k: np.vstack(v) for k, v in out.items()}


def utilization(kind, idx, temp, compute):
    n = len(idx)
    h = idx.hour.to_numpy()
    if kind == 'ai_training':
        u = 0.92 + ar1_noise(n, 0.04)
        for d in np.flatnonzero(RNG.random(n // 24) < 0.05):            # job restarts / checkpoint dips
            s = d * 24 + RNG.integers(0, 20)
            u[s:s + RNG.integers(2, 7)] = 0.45
    elif kind == 'ai_inference':
        # user-facing serving: shape and noise learned from Google traces, most user-facing workload mix
        u = 0.80 * compute.simulate(idx, compute.share_for('ai_inference'))
    else:                                                               # bitcoin mining
        u = 0.97 + ar1_noise(n, 0.01)
        t = temp.to_numpy()
        grid_stress = ((t >= 29) & (h >= 13) & (h <= 19)) | ((t <= -15) & (((h >= 7) & (h <= 10)) | ((h >= 16) & (h <= 20))))
        economic = RNG.random(n) < 0.03
        u[grid_stress | economic] = 0.10                                # curtails for grid / price signals
    u *= np.repeat(RNG.random(n // 24 + 1) > 0.003, 24)[:n]              # rare full outages (~1 day/yr)
    return np.clip(u, 0, 1)


def simulate_site2_supply(temp, phase_mw, kind, compute):
    cfg = SITE2_COMPUTE[kind]
    it_cap = phase_mw / SITE2_PUE
    out = {k: [] for k in ['it_mw', 'source_heat_mw', 'delivered_heat_mw', 'hp_elec_mw', 'cop']}
    for y in temp.index.year.unique():
        t = temp[temp.index.year == y]
        cop, _ = heat_pump(cfg['source_c'], t.to_numpy())
        for _ in range(N_DRAWS_PER_YEAR):
            it = it_cap * utilization(kind, t.index, t, compute)
            src = it * cfg['capture'] * RNG.uniform(0.9, 1.05)
            delivered = src * cop / (cop - 1)
            for k, v in [('it_mw', it), ('source_heat_mw', src), ('delivered_heat_mw', delivered),
                         ('hp_elec_mw', delivered / cop), ('cop', cop)]:
                out[k].append(v)
    return {k: np.vstack(v) for k, v in out.items()}


def pct_frame(sim, prefix=''):
    cols = {}
    for k, v in sim.items():
        for p in (10, 50, 90):
            cols[f'{prefix}{k}_p{p}'] = np.percentile(v, p, axis=0)
    return pd.DataFrame(cols)


# ======================================================================
# MAIN
# ======================================================================
def typical_index():
    i = pd.date_range('2023-01-01', periods=8760, freq='h')
    return i


if __name__ == '__main__':
    pd.set_option('display.width', 200)
    section('1. DEMAND MODEL: training on NREL ComStock / ResStock hourly simulations')
    nrel_rows, blend_temp = load_nrel_training()
    train = training_frame(nrel_rows)
    print(f"{train.groupby(['region', 'btype']).ngroups} building-type series, {len(train):,} training rows")
    val, widen = validate(train)
    print('\n'.join(val))
    model = DemandModel(train).fit(train)
    model.widen = widen
    (OUT / 'demand_model_validation.txt').write_text('\n'.join(val))

    idx = typical_index()
    results = {}
    for key, cfg in SITES.items():
        section(f"2. {key.upper()} DEMAND: {cfg['name']}")
        b = site1_buildings() if key == 'site1' else site2_buildings(nrel_rows, blend_temp)
        shapes, temp = typical_shapes(model, cfg['station'], sorted(b['nrel_type'].unique()))
        agg = aggregate_demand(b, shapes, cfg['rings_m'])
        b = building_summaries(b, shapes)
        b.to_csv(OUT / f'{key}_building_demand_summary.csv', index=False)
        # hourly shapes (P50, every weather year) so other scripts can rebuild any building's hourly demand
        np.savez_compressed(OUT / f'{key}_demand_shapes.npz',
                            **{f'{bt}|{comp}': shapes[(bt, comp)][0.5].astype(np.float32)
                               for (bt, comp) in shapes})

        dem = pd.DataFrame({'timestamp': idx, 'outdoor_c_p50': np.median(temp.to_numpy().reshape(len(WEATHER_YEARS), 8760), axis=0)})
        for r in cfg['rings_m']:
            for p in ['P10', 'P50', 'P90']:
                dem[f'demand_mw_le{r}m_{p}'] = agg[r][p]
        dem.to_csv(OUT / f'{key}_demand_hourly_typical_year.csv', index=False)

        # hourly ranges for the 25 largest / best-scored candidates (full detail for any building: building_hourly_kw)
        rank_col = 'suitability_score' if 'suitability_score' in b else 'annual_mmbtu'
        top = b.sort_values(rank_col, ascending=False).head(25)
        wide = {'timestamp': idx}
        for _, r in top.iterrows():
            h = building_hourly_kw(r, shapes)
            label = f"{str(r['name'])[:30]} [{r['id']}]"
            for q, tag in zip(QUANTILES, ['P10', 'P50', 'P90']):
                wide[f'{label} kW {tag}'] = h[q].round(1)
        pd.DataFrame(wide).to_csv(OUT / f'{key}_top25_buildings_hourly_kw.csv', index=False)

        print(f"{len(b):,} buildings modelled")
        rows = []
        summer = slice(151 * 24, 243 * 24)
        for r in cfg['rings_m']:
            sub = b[b['dist_m'] <= r]
            rows.append({'ring': f'<= {r} m', 'buildings': len(sub),
                         'annual_GWh_heat': sub['annual_mmbtu'].sum() / 3412.14,
                         'mean_MW_P50': agg[r]['P50'].mean(), 'peak_MW_P50': agg[r]['P50'].max(),
                         'peak_MW_P90': agg[r]['P90'].max(), 'summer_min_MW_P10': agg[r]['P10'][summer].min()})
        print(pd.DataFrame(rows).round(1).to_string(index=False))
        results[key] = dict(buildings=b, agg=agg, temp=temp, demand=dem)

    section('3a. COMPUTE LOAD MODEL: learned from Google 2019 data-center power traces')
    compute = ComputeLoadModel()
    trace_lines = compute.validate() + [compute.describe()]
    print('\n'.join(trace_lines))
    for kind in WORKLOAD_MIX_PCTILE:
        print(f"  workload mix used for {kind}: production share {compute.share_for(kind):.2f}")
    with open(OUT / 'demand_model_validation.txt', 'a') as fh:
        fh.write('\n' + '\n'.join(trace_lines))
    hourly = pd.DataFrame({'hour': range(24)})
    for kind in WORKLOAD_MIX_PCTILE:
        for q in QUANTILES:
            X = pd.DataFrame({'hrs_after_trough': (hourly['hour'] - TROUGH_LOCAL_HOUR) % 24, 'dow': 2,
                              'production_share': compute.share_for(kind), 'is_mvpp': 0})
            hourly[f'{kind}_rel_load_P{int(q * 100)}'] = compute.models[q].predict(X[ComputeLoadModel.FEATS])
    hourly.to_csv(OUT / 'compute_load_profile_by_hour.csv', index=False)

    section('3b. SITE 1 SUPPLY: 111 8th Ave (colocation / carrier hotel)')
    fit = fit_site1_electricity()
    print(f"Monthly regression on {fit['n']} months: R2={fit['r2']:.2f}, cooling starts at {fit['tbal']:.1f} C, "
          f"+{fit['b']:.2f} MW per C above it (SE {fit['se_b']:.2f})")
    print('Non-cooling load by year (MW): ' + ', '.join(f"{y}: {v:.1f}" for y, v in fit['level'].items()))
    sim1 = simulate_site1_supply(fit, results['site1']['temp'], compute)
    s1 = pd.concat([pd.DataFrame({'timestamp': idx}), pct_frame(sim1)], axis=1)
    s1.to_csv(OUT / 'site1_supply_hourly_typical_year.csv', index=False)
    np.savez_compressed(OUT / 'site1_supply_runs.npz', **{k: v.astype(np.float32) for k, v in sim1.items()})
    for k in ['total_mw', 'it_mw', 'source_heat_mw', 'delivered_heat_mw', 'hp_elec_mw', 'cop']:
        v = sim1[k]
        print(f"  {k:18s} mean {v.mean():6.2f}  hourly P10 {np.percentile(v, 10):6.2f}  P90 {np.percentile(v, 90):6.2f}  "
              f"min {v.min():6.2f}  max {v.max():6.2f}")

    section('4. SITE 2 SUPPLY: Lake Hawkeye (proposed) - compute-type scenarios')
    s2_frames = [pd.DataFrame({'timestamp': idx})]
    sims2 = {}
    for phase, mw in SITE2_PHASES_MW.items():
        for kind in SITE2_COMPUTE:
            sim = simulate_site2_supply(results['site2']['temp'], mw, kind, compute)
            sims2[(phase, kind)] = sim
            s2_frames.append(pct_frame(sim, prefix=f'{phase}_{kind}__'))
            d = sim['delivered_heat_mw']
            print(f"  {phase} ({mw} MW) {kind:15s} IT mean {sim['it_mw'].mean():6.1f} MW | delivered heat mean "
                  f"{d.mean():6.1f} MW, P10 hour {np.percentile(d, 10):6.1f}, min {d.min():6.1f} | COP {sim['cop'].mean():.1f}")
    pd.concat(s2_frames, axis=1).to_csv(OUT / 'site2_supply_hourly_typical_year.csv', index=False)
    np.savez_compressed(OUT / 'site2_supply_runs.npz',
                        **{f'{phase}|{kind}|{k}': v.astype(np.float32)
                           for (phase, kind), sim in sims2.items() for k, v in sim.items()})

    section('5. MATCH: share of each hour\'s demand the data center can cover (P50 supply vs P50 demand)')
    def match(sup, agg, rings, label):
        for r in rings:
            dem = agg[r]['P50']
            cover = np.minimum(sup, dem).sum() / dem.sum()
            used = np.minimum(sup, dem).sum() / sup.sum()
            print(f"  {label:38s} <= {r:5d} m: demand covered {cover:5.0%} | supply used {used:5.0%} | "
                  f"hours supply < demand {np.mean(sup < dem):5.0%}")
    match(np.median(sim1['delivered_heat_mw'], axis=0), results['site1']['agg'], SITES['site1']['rings_m'], 'Site 1')
    for (phase, kind), sim in sims2.items():
        if phase == 'phase1':
            match(np.median(sim['delivered_heat_mw'], axis=0), results['site2']['agg'],
                  SITES['site2']['rings_m'], f'Site 2 {phase} {kind}')

    print(f"\nOutputs in {OUT}")
