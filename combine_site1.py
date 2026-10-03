"""
Site 1 off-taker table: every building within 2.2 km of 111 8th Ave, with heat
demand, timing, fuel, cost, carbon and equity attributes in one row each.

Run from the project folder:  python combine_site1.py
Writes outputs/site1_offtakers_2200m.csv and .geojson (for GIS / mapping).

How the datasets are combined
  LL84 annual (measured energy, buildings > 25k ft2) -- the backbone
    + LL84 monthly        -> seasonality (base-load share, winter/summer ratio)
    + PLUTO (by BBL)      -> building class, owner, units, floors, census tract
    + PLUTO lots NOT in LL84 -> smaller buildings, heat ESTIMATED from LL84 medians
    + school locations (by BBL), NYCHA (PLUTO owner), NYS DAC tracts (spatial join)
    + NYS energy prices   -> buyer's current heat cost X ($/MMBtu useful heat)
    + LL97 coefficients   -> thermal CO2 and penalty exposure
"""
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import wkt

from data_load import load_ll84, HEAT_COLS, GFA

DATA = Path(__file__).parent / 'heat-reuse-data' / 'data'
OUT = Path(__file__).parent / 'outputs'
OUT.mkdir(exist_ok=True)

SITE_ID = 7536925                          # LL84 Property ID of 111 8th Ave
SITE_LAT, SITE_LON = 40.740704, -74.001844
RADIUS_M = 2200
RINGS_M = [250, 500, 1000, 1500, 2200]

# ---------------- ASSUMPTIONS (edit these) ----------------
BOILER_EFF = 0.80              # fuel -> useful heat for gas / oil boilers
STEAM_EFF = 0.90               # purchased steam -> useful heat
EFLH = 2500                    # equivalent full-load hours/yr: peak kW = annual heat / EFLH
STEAM_USD_PER_MMBTU = None     # Con Ed steam price is not in the data; set it to fill X for steam users
LL97_PENALTY = 268             # $/tCO2e
EF = {'gas': 0.00005311, 'steam': 0.00004493, 'dhw': 0.00004493,   # LL97 2024-29, tCO2e/kBtu
      'oil2': 0.00007421, 'oil4': 0.00007529, 'oil56': 0.00007529}
SCORE_WEIGHTS = {'demand': 0.30, 'proximity': 0.25, 'baseload': 0.15,
                 'fuel_value': 0.10, 'equity': 0.20}
# ---------------------------------------------------------

FUEL_SHORT = dict(zip(HEAT_COLS, ['gas', 'steam', 'dhw', 'oil2', 'oil4', 'oil56']))
PLUTO_CLASS = {'A': 'One-family', 'B': 'Two-family', 'C': 'Walk-up apartments',
               'D': 'Elevator apartments', 'E': 'Warehouse', 'F': 'Factory', 'G': 'Garage',
               'H': 'Hotel', 'I': 'Health care', 'J': 'Theatre', 'K': 'Store', 'L': 'Loft',
               'M': 'Religious', 'N': 'Asylum/home', 'O': 'Office', 'P': 'Assembly',
               'Q': 'Recreation', 'R': 'Condominium', 'S': 'Mixed residential/store',
               'T': 'Transportation', 'U': 'Utility', 'V': 'Vacant', 'W': 'Education',
               'Y': 'Government', 'Z': 'Miscellaneous'}


def haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6_371_000 * 2 * np.arcsin(np.sqrt(a))


def parse_bbls(value):
    """'1-01377-0047' / '1004720015, 1004720010' / '3004650033 ; 3004650029' -> list of 10-digit BBLs."""
    return re.findall(r'\d{10}', str(value).replace('-', ''))


def category(use_type):
    t = str(use_type).lower()
    for key, cat in [('multifamily', 'Residential'), ('residence hall', 'Residential'),
                     ('apartment', 'Residential'), ('family', 'Residential'), ('condominium', 'Residential'),
                     ('mixed residential', 'Residential'), ('senior', 'Residential'), ('asylum', 'Residential'),
                     ('hospital', 'Healthcare'), ('health', 'Healthcare'), ('medical', 'Healthcare'),
                     ('school', 'Education'), ('college', 'Education'), ('education', 'Education'),
                     ('hotel', 'Hotel'), ('office', 'Office'), ('data center', 'Data center')]:
        if key in t:
            return cat
    return 'Other'


# ======================================================================
# 1. LL84 measured buildings within the radius
# ======================================================================
ll = load_ll84()
ll['dist_m'] = haversine_m(SITE_LAT, SITE_LON, ll['Latitude'], ll['Longitude'])
ll = ll[ll['dist_m'] <= RADIUS_M].copy()
ll['bbls'] = ll['NYC Borough, Block and Lot (BBL)'].apply(parse_bbls)
ll = ll.rename(columns=FUEL_SHORT)
fuels = list(FUEL_SHORT.values())
ll[fuels] = ll[fuels].fillna(0)

# ======================================================================
# 2. PLUTO lots within the radius
# ======================================================================
pluto = pd.read_csv(DATA / 'site1_nyc' / 'pluto_manhattan.csv', low_memory=False,
                    usecols=['bbl', 'address', 'bldgclass', 'landuse', 'ownertype', 'ownername',
                             'unitsres', 'numfloors', 'yearbuilt', 'yearalter1', 'yearalter2',
                             'bldgarea', 'resarea', 'comarea', 'latitude', 'longitude',
                             'bct2020', 'histdist', 'landmark'])
pluto['bbl'] = pluto['bbl'].astype('Int64').astype(str)
pluto['dist_m'] = haversine_m(SITE_LAT, SITE_LON, pluto['latitude'], pluto['longitude'])
pluto = pluto[pluto['dist_m'] <= RADIUS_M].copy()
pluto['is_nycha'] = pluto['ownername'].str.contains('HOUSING AUTHORITY', case=False, na=False)
pluto['last_upgrade'] = pluto[['yearbuilt', 'yearalter1', 'yearalter2']].max(axis=1)

schools = pd.read_csv(DATA / 'site1_nyc' / 'school_locations.csv', low_memory=False,
                      usecols=['location_name', 'Borough_block_lot'])
schools['bbl'] = schools['Borough_block_lot'].astype('Int64').astype(str)
school_names = schools.groupby('bbl')['location_name'].apply(lambda s: '; '.join(sorted(set(s))))
pluto['school_names'] = pluto['bbl'].map(school_names)

# attach PLUTO attributes to each LL84 record (a campus can span many lots)
link = ll[['Property ID', 'bbls']].explode('bbls').rename(columns={'bbls': 'bbl'})
link = link.merge(pluto, on='bbl', how='inner')
pl_agg = link.groupby('Property ID').agg(
    n_lots=('bbl', 'nunique'), bldgclass=('bldgclass', 'first'), owner=('ownername', 'first'),
    res_units=('unitsres', 'sum'), floors=('numfloors', 'max'), last_upgrade=('last_upgrade', 'max'),
    is_nycha=('is_nycha', 'any'), landmark=('landmark', lambda s: s.notna().any()),
    school_names=('school_names', lambda s: '; '.join(s.dropna())))
ll = ll.merge(pl_agg, left_on='Property ID', right_index=True, how='left')
print(f"LL84 records within {RADIUS_M} m: {len(ll):,}; matched to PLUTO lots: {ll['n_lots'].notna().sum():,}")

# ======================================================================
# 3. LL84 monthly -> seasonality per building
# ======================================================================
mcols = {'District Steam Use  (kBtu)': 'steam', 'Natural Gas Use - Monthly (kBtu)': 'gas',
         'Fuel Oil #2 Use - Monthly (kBtu)': 'oil2', 'Fuel Oil #4 Use - Monthly (kBtu)': 'oil4',
         'Fuel Oil #5&6 Use - Monthly (kBtu)': 'oil56'}
mon = pd.read_csv(DATA / 'site1_nyc' / 'll84_monthly.csv', low_memory=False,
                  usecols=['Calendar Year', 'Month', 'Property Id', *mcols])
mon = mon[mon['Property Id'].isin(set(ll['Property ID']))].rename(columns=mcols)
mon[list(mcols.values())] = mon[list(mcols.values())].apply(pd.to_numeric, errors='coerce')
mon['heat'] = mon[list(mcols.values())].sum(axis=1, min_count=1)
mon['m'] = pd.to_datetime(mon['Month'], format='%y-%b').dt.month
mon = mon.dropna(subset=['heat']).drop_duplicates(['Property Id', 'Calendar Year', 'm'])
full_years = mon.groupby(['Property Id', 'Calendar Year'])['m'].transform('nunique') == 12
mon = mon[full_years]
latest = mon.groupby('Property Id')['Calendar Year'].transform('max')
mon = mon[mon['Calendar Year'] == latest]
piv = mon.pivot_table(index='Property Id', columns='m', values='heat', aggfunc='sum')
summer, winter = piv[[6, 7, 8]].mean(axis=1), piv[[12, 1, 2]].mean(axis=1)
season = pd.DataFrame({
    'monthly_year': mon.groupby('Property Id')['Calendar Year'].first(),
    # lowest month = hot water / process load that runs all year. Using the minimum (usually a
    # shoulder month) rather than the summer average avoids counting steam-driven cooling as heat.
    'base_load_share': (piv.min(axis=1) * 12 / piv.sum(axis=1)).clip(0, 1),
    'winter_summer_ratio': winter / summer.replace(0, np.nan)})
ll = ll.merge(season, left_on='Property ID', right_index=True, how='left')
print(f"Monthly profiles found for {ll['base_load_share'].notna().sum():,} LL84 records")

# ======================================================================
# 4. Smaller buildings: PLUTO lots with no LL84 record -> estimated heat
# ======================================================================
covered = set(link['bbl'])
small = pluto[~pluto['bbl'].isin(covered) & (pluto['bldgarea'] > 0)
              & ~pluto['landuse'].isin([7, 10, 11])].copy()       # not transport, parking, vacant
ll_heat = ll[(ll['heat_fuel_kbtu'] > 0) & ll['bldgclass'].notna()]
intensity = ll_heat.groupby(ll_heat['bldgclass'].str[0])['heat_kbtu_per_ft2'].median()
small['heat_kbtu_per_ft2'] = (small['bldgclass'].str[0].map(intensity)
                              .fillna(ll_heat['heat_kbtu_per_ft2'].median()))
small['heat_fuel_kbtu'] = small['bldgarea'] * small['heat_kbtu_per_ft2']
print(f"PLUTO lots without LL84 data: {len(small):,} (heat estimated from LL84 median intensity by class)")

# ======================================================================
# 5. One unified table
# ======================================================================
measured = pd.DataFrame({
    'record_type': 'LL84 measured',
    'id': ll['Property ID'].astype(str),
    'name': ll['Property Name'],
    'address': ll['Address 1'],
    'bbl': ll['bbls'].str.join(';'),
    'lat': ll['Latitude'], 'lon': ll['Longitude'], 'dist_m': ll['dist_m'],
    'use_type': ll['Primary Property Type - Self Selected'],
    'pluto_class': ll['bldgclass'],
    'gfa_ft2': ll[GFA], 'year_built': ll['Year Built'], 'last_upgrade': ll['last_upgrade'],
    'floors': ll['floors'], 'res_units': ll['res_units'], 'owner': ll['owner'],
    'is_nycha': ll['is_nycha'].eq(True),
    'school_names': ll['school_names'].replace('', np.nan),
    'is_landmark': ll['landmark'].eq(True),
    'data_year': ll['Calendar Year'],
    **{f'{f}_kbtu': ll[f] for f in fuels},
    'heat_fuel_kbtu': ll['heat_fuel_kbtu'],
    'base_load_share': ll['base_load_share'], 'winter_summer_ratio': ll['winter_summer_ratio'],
    'is_source': ll['Property ID'] == SITE_ID,
})
estimated = pd.DataFrame({
    'record_type': 'PLUTO estimated',
    'id': small['bbl'], 'name': np.nan, 'address': small['address'], 'bbl': small['bbl'],
    'lat': small['latitude'], 'lon': small['longitude'], 'dist_m': small['dist_m'],
    'use_type': small['bldgclass'].str[0].map(PLUTO_CLASS), 'pluto_class': small['bldgclass'],
    'gfa_ft2': small['bldgarea'], 'year_built': small['yearbuilt'], 'last_upgrade': small['last_upgrade'],
    'floors': small['numfloors'], 'res_units': small['unitsres'], 'owner': small['ownername'],
    'is_nycha': small['is_nycha'], 'school_names': small['school_names'],
    'is_landmark': small['landmark'].notna(), 'data_year': np.nan,
    **{f'{f}_kbtu': np.nan for f in fuels},
    'heat_fuel_kbtu': small['heat_fuel_kbtu'],
    'base_load_share': np.nan, 'winter_summer_ratio': np.nan, 'is_source': False,
})
df = pd.concat([measured, estimated], ignore_index=True)
df['category'] = df['use_type'].apply(category)
df['is_school'] = df['school_names'].notna()
df['ring'] = pd.cut(df['dist_m'], [-1, *RINGS_M], labels=[f'<= {r} m' for r in RINGS_M])

# useful heat, peak, timing
fk = [f'{f}_kbtu' for f in fuels]
eff = {'gas_kbtu': BOILER_EFF, 'oil2_kbtu': BOILER_EFF, 'oil4_kbtu': BOILER_EFF,
       'oil56_kbtu': BOILER_EFF, 'steam_kbtu': STEAM_EFF, 'dhw_kbtu': 1.0}
measured_useful = sum(df[c].fillna(0) * e for c, e in eff.items()) / 1000
df['useful_heat_mmbtu'] = np.where(df['record_type'] == 'LL84 measured', measured_useful,
                                   df['heat_fuel_kbtu'] * BOILER_EFF / 1000)
df['peak_kw_est'] = df['useful_heat_mmbtu'] * 1e6 / 3412 / EFLH
df['base_load_share_filled'] = df['base_load_share'].fillna(
    df.groupby('category')['base_load_share'].transform('median')).fillna(df['base_load_share'].median())
df['base_load_mmbtu'] = df['useful_heat_mmbtu'] * df['base_load_share_filled']

# fuel shares and main fuel (measured only)
tot = df[fk].sum(axis=1).replace(0, np.nan)
df['share_gas'] = df['gas_kbtu'] / tot
df['share_steam'] = (df['steam_kbtu'] + df['dhw_kbtu']) / tot
df['share_oil'] = df[['oil2_kbtu', 'oil4_kbtu', 'oil56_kbtu']].sum(axis=1) / tot
df['main_fuel'] = 'unknown'
has_fuel = tot.notna()
df.loc[has_fuel, 'main_fuel'] = (df.loc[has_fuel, ['share_gas', 'share_steam', 'share_oil']]
                                 .fillna(0).idxmax(axis=1).str.replace('share_', ''))

# buyer's current cost X ($ per MMBtu of useful heat), NYS SEDS latest year
prices = pd.read_csv(DATA / 'prices' / 'nys_energy_prices_usd_per_mmbtu.csv')
p = prices[prices['Year'] == prices['Year'].max()].set_index('Sector')
is_res = df['category'] == 'Residential'
gas_price = np.where(is_res, p.loc['Residential', 'Natural Gas'], p.loc['Commercial', 'Natural Gas'])
oil_price = np.where(is_res, p.loc['Residential', 'Distillate'], p.loc['Commercial', 'Distillate'])
steam_price = np.nan if STEAM_USD_PER_MMBTU is None else STEAM_USD_PER_MMBTU
spend = (df['gas_kbtu'].fillna(0) * gas_price
         + df[['oil2_kbtu', 'oil4_kbtu', 'oil56_kbtu']].sum(axis=1) * oil_price
         + (df['steam_kbtu'].fillna(0) + df['dhw_kbtu'].fillna(0)) * steam_price) / 1000
spend = spend.where(~((df['share_steam'] > 0) & np.isnan(steam_price)))   # unknown if steam unpriced
spend = spend.where(df['record_type'] == 'LL84 measured', df['heat_fuel_kbtu'] / 1000 * gas_price)
df['annual_heat_spend_usd'] = spend
df['current_cost_X_usd_per_mmbtu'] = spend / df['useful_heat_mmbtu'].replace(0, np.nan)

# carbon
df['thermal_co2_t'] = np.where(df['record_type'] == 'LL84 measured',
                               sum(df[f'{f}_kbtu'].fillna(0) * EF[f] for f in fuels),
                               df['heat_fuel_kbtu'] * EF['gas'])
df['ll97_exposure_usd'] = df['thermal_co2_t'] * LL97_PENALTY

# equity: NYS Disadvantaged Community tract (spatial join on the building point)
dac = pd.read_csv(DATA / 'equity' / 'nys_dac_2023.csv', low_memory=False,
                  usecols=['the_geom', 'GEOID', 'DAC_Designation', 'County', 'LMI_80_AMI',
                           'Renter_Percent', 'Asthma_ED_Rate', 'Percentile_Rank_Combined_NYC'])
dac = dac[dac['County'].str.contains('New York', na=False)]
dac = gpd.GeoDataFrame(dac.drop(columns='the_geom'), geometry=dac['the_geom'].apply(wkt.loads), crs='EPSG:4326')
gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df['lon'], df['lat']), crs='EPSG:4326')
gdf = gpd.sjoin(gdf, dac, how='left', predicate='within').drop(columns='index_right')
gdf = gdf[~gdf.index.duplicated()]
gdf = gdf.rename(columns={'GEOID': 'tract_geoid', 'Percentile_Rank_Combined_NYC': 'dac_pctile_nyc'})
gdf['is_dac'] = gdf['DAC_Designation'].eq('Designated as DAC')

# ======================================================================
# 6. Suitability score (transparent; change SCORE_WEIGHTS to test other priorities)
# ======================================================================
g = gdf
comp = pd.DataFrame(index=g.index)
comp['demand'] = g['useful_heat_mmbtu'].rank(pct=True)
comp['proximity'] = 1 - g['dist_m'] / RADIUS_M
comp['baseload'] = g['base_load_share_filled'] / g['base_load_share_filled'].max()
comp['fuel_value'] = (g['share_oil'].fillna(0) * 1.0 + g['share_steam'].fillna(0) * 0.8
                      + g['share_gas'].fillna(1) * 0.5)
comp['equity'] = (g['is_dac'] | g['is_nycha']).astype(float).clip(upper=1) * 0.7 + g['is_school'] * 0.3
g['suitability_score'] = 100 * sum(comp[k] * w for k, w in SCORE_WEIGHTS.items())
g.loc[g['useful_heat_mmbtu'] <= 0, 'suitability_score'] = 0
g = g.sort_values('suitability_score', ascending=False)

g.drop(columns='geometry').to_csv(OUT / 'site1_offtakers_2200m.csv', index=False)
g.to_file(OUT / 'site1_offtakers_2200m.geojson', driver='GeoJSON')

# ======================================================================
# 7. Summary
# ======================================================================
pd.set_option('display.width', 200)
src_heat_mmbtu = 437_000   # deliverable from site1_analysis.py (14.6 MW after heat pump)
print(f"\nTotal records: {len(g):,} ({(g.record_type == 'LL84 measured').sum():,} measured, "
      f"{(g.record_type == 'PLUTO estimated').sum():,} estimated)")

by_ring = g.groupby('ring', observed=False).agg(
    buildings=('id', 'count'), useful_heat_mmbtu=('useful_heat_mmbtu', 'sum'),
    base_load_mmbtu=('base_load_mmbtu', 'sum'), peak_mw=('peak_kw_est', lambda s: s.sum() / 1000),
    co2_t=('thermal_co2_t', 'sum')).cumsum()
by_ring['heat_vs_supply'] = by_ring['useful_heat_mmbtu'] / src_heat_mmbtu
print('\nCumulative demand by distance:')
print(by_ring.round(1).to_string())

by_cat = g.groupby('category').agg(
    buildings=('id', 'count'), useful_heat_mmbtu=('useful_heat_mmbtu', 'sum'),
    median_base_load=('base_load_share', 'median'), median_X=('current_cost_X_usd_per_mmbtu', 'median'),
    co2_t=('thermal_co2_t', 'sum')).sort_values('useful_heat_mmbtu', ascending=False)
print('\nBy category:')
print(by_cat.round(2).to_string())

print('\nEquity flags:')
for flag in ['is_dac', 'is_nycha', 'is_school']:
    sub = g[g[flag]]
    print(f"  {flag:9s} {len(sub):5,} buildings, {sub['useful_heat_mmbtu'].sum():>11,.0f} MMBtu/yr useful heat")

show = ['name', 'address', 'record_type', 'category', 'dist_m', 'useful_heat_mmbtu', 'peak_kw_est',
        'base_load_share', 'main_fuel', 'is_dac', 'is_nycha', 'is_school', 'suitability_score']
print('\nTop 25 by suitability score:')
print(g[show].head(25).round(2).to_string(index=False, max_colwidth=26))
print(f"\nWrote {OUT / 'site1_offtakers_2200m.csv'} and .geojson")
if STEAM_USD_PER_MMBTU is None:
    print('NOTE: STEAM_USD_PER_MMBTU not set -> cost X is blank for steam-heated buildings.')
