"""
Site 1 (111 8th Ave, Manhattan) heat-reuse analysis.

Run from the project folder:  python site1_analysis.py
Prints a summary and writes tables to ./outputs/.

Every number that is an assumption (not from a dataset) is in the ASSUMPTIONS
block below so the team can change it and re-run.
"""
import glob
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent / 'heat-reuse-data' / 'data'
OUT = Path(__file__).parent / 'outputs'
OUT.mkdir(exist_ok=True)

SITE_ID = 7536925                      # LL84 Property ID of 111 8th Ave
SITE_LAT, SITE_LON = 40.740704, -74.001844
RADII_M = [250, 500, 1000, 1500]

# ---------------- ASSUMPTIONS (edit these) ----------------
NON_DC_KWH_PER_SQFT = 13.0   # electricity intensity of the office/other space in the building (CBECS-ish office)
RECOVERABLE_FRAC = 0.80      # share of data-center electricity we can capture as heat in a water loop
SOURCE_TEMP_C = 30           # air-cooled / rear-door return water temperature
HP_COP = 3.0                 # heat pump COP lifting ~30 C -> ~65-70 C (4th-gen DH supply)
BOILER_EFF = 0.80            # buyer's existing boiler efficiency (fuel -> useful heat)
STEAM_EFF = 0.90             # useful heat per unit of purchased Con Ed steam
LL97_PENALTY = 268           # $/tCO2e over the LL97 limit
# LL97 2024-2029 emission coefficients (tCO2e per kBtu, electricity per kWh)
EF = {'gas': 0.00005311, 'steam': 0.00004493, 'oil2': 0.00007421, 'oil4': 0.00007529,
      'oil56': 0.00007529, 'elec_kwh': 0.000288962}
KBTU_PER_KWH = 3.412
# ---------------------------------------------------------

FUELS = {  # LL84 annual column -> short name
    'Natural Gas Use (kBtu)': 'gas',
    'District Steam Use (kBtu)': 'steam',
    'District Hot Water Use (kBtu)': 'dhw_district',
    'Fuel Oil #2 Use (kBtu)': 'oil2',
    'Fuel Oil #4 Use (kBtu)': 'oil4',
    'Fuel Oil #5 & 6 Use (kBtu)': 'oil56',
}
MAX_HEAT_INTENSITY = 400     # kBtu/ft2/yr of heating fuel; above this the record is a campus/plant meter, not one building
ANNUAL_COLS = ['Calendar Year', 'Property ID', 'Parent Property ID', 'Property Name', 'Address 1', 'Postal Code',
               'NYC Borough, Block and Lot (BBL)', 'Latitude', 'Longitude',
               'Primary Property Type - Self Selected', 'Largest Property Use Type',
               'Property GFA - Calculated (Buildings) (ft²)', 'Year Built',
               'Electricity Use - Grid Purchase (kWh)',
               'Total (Location-Based) GHG Emissions (Metric Tons CO2e)', *FUELS]


def haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6_371_000 * 2 * np.arcsin(np.sqrt(a))


def section(title):
    print('\n' + '=' * 78 + '\n' + title + '\n' + '=' * 78)


# ======================================================================
# 1. Load LL84 annual: one row per building, most recent year
# ======================================================================
ll84 = pd.read_csv(DATA / 'site1_nyc' / 'll84_annual_2022_present.csv',
                   usecols=ANNUAL_COLS, low_memory=False)
num_cols = ['Latitude', 'Longitude', 'Property GFA - Calculated (Buildings) (ft²)', 'Year Built',
            'Electricity Use - Grid Purchase (kWh)',
            'Total (Location-Based) GHG Emissions (Metric Tons CO2e)', *FUELS]
ll84[num_cols] = ll84[num_cols].apply(pd.to_numeric, errors='coerce')  # "Not Available" -> NaN
ll84 = (ll84.drop_duplicates()
            .sort_values('Calendar Year')
            .drop_duplicates('Property ID', keep='last')
            .rename(columns={**FUELS,
                             'Property GFA - Calculated (Buildings) (ft²)': 'gfa_ft2',
                             'Primary Property Type - Self Selected': 'type',
                             'Electricity Use - Grid Purchase (kWh)': 'elec_kwh',
                             'Total (Location-Based) GHG Emissions (Metric Tons CO2e)': 'ghg_t'}))
fuel_names = list(FUELS.values())
ll84[fuel_names] = ll84[fuel_names].fillna(0)
ll84['thermal_fuel_mmbtu'] = ll84[fuel_names].sum(axis=1) / 1000
# useful heat the building actually needs = fuel x appliance efficiency
ll84['useful_heat_mmbtu'] = ((ll84[['gas', 'oil2', 'oil4', 'oil56']].sum(axis=1) * BOILER_EFF
                              + ll84['steam'] * STEAM_EFF + ll84['dhw_district']) / 1000)
ll84['thermal_co2_t'] = sum(ll84[f] * EF[f] for f in ['gas', 'steam', 'oil2', 'oil4', 'oil56'])
ll84['dist_m'] = haversine_m(SITE_LAT, SITE_LON, ll84['Latitude'], ll84['Longitude'])

# Campus double counting: LL84 reports parent campuses AND their child buildings, often with the
# campus total copied onto one child. Drop parents, then identical address+energy repeats, then
# records whose heating intensity is impossible for a single building (central-plant meters).
n0 = len(ll84)
parents = set(pd.to_numeric(ll84['Parent Property ID'], errors='coerce').dropna().astype(int))
ll84 = ll84[~ll84['Property ID'].isin(parents)]
has_heat = ll84['thermal_fuel_mmbtu'] > 0
ll84 = pd.concat([ll84[~has_heat],
                  ll84[has_heat].drop_duplicates(['Address 1', 'thermal_fuel_mmbtu'])])
ll84['heat_kbtu_ft2'] = ll84['thermal_fuel_mmbtu'] * 1000 / ll84['gfa_ft2']
campus_meters = ll84[ll84['heat_kbtu_ft2'] > MAX_HEAT_INTENSITY]
ll84 = ll84[~(ll84['heat_kbtu_ft2'] > MAX_HEAT_INTENSITY)]
print(f"De-duplicated LL84: {n0:,} -> {len(ll84):,} buildings "
      f"({len(campus_meters)} campus/plant meters set aside, e.g. "
      f"{', '.join(campus_meters.nsmallest(3, 'dist_m')['Property Name'].astype(str))})")

# ======================================================================
# 2. The heat source
# ======================================================================
section('1. HEAT SOURCE: 111 8th Ave')
site = ll84[ll84['Property ID'] == SITE_ID].iloc[0]
site_raw = pd.read_csv(DATA / 'site1_nyc' / 'll84_111_8th_ave.csv')
dc_ft2 = site_raw['Data Center - Gross Floor Area (ft²)'].iloc[-1]
elec_kwh = site['elec_kwh']
non_dc_kwh = (site['gfa_ft2'] - dc_ft2) * NON_DC_KWH_PER_SQFT
dc_kwh = elec_kwh - non_dc_kwh
recov_mwh = dc_kwh * RECOVERABLE_FRAC / 1000
recov_mw = recov_mwh / 8760
delivered_mw = recov_mw * HP_COP / (HP_COP - 1)       # source heat + compressor work
delivered_mmbtu = delivered_mw * 8760 * 3.412
hp_elec_mwh = delivered_mw * 8760 / HP_COP

print(f"Reporting year {int(site['Calendar Year'])}, GFA {site['gfa_ft2']:,.0f} ft2, data center {dc_ft2:,.0f} ft2")
print(f"Grid electricity           {elec_kwh/1e6:8.1f} GWh/yr = {elec_kwh/8760/1000:5.1f} MW average")
print(f"  est. non-DC (office etc) {non_dc_kwh/1e6:8.1f} GWh/yr  (assumes {NON_DC_KWH_PER_SQFT} kWh/ft2)")
print(f"  est. data center         {dc_kwh/1e6:8.1f} GWh/yr = {dc_kwh/8760/1000:5.1f} MW")
print(f"Recoverable low-grade heat {recov_mw:8.1f} MW at ~{SOURCE_TEMP_C} C ({RECOVERABLE_FRAC:.0%} of DC power)")
print(f"After heat pump (COP {HP_COP})  {delivered_mw:8.1f} MW = {delivered_mmbtu:,.0f} MMBtu/yr at ~65-70 C")
print(f"  heat-pump electricity    {hp_elec_mwh/1000:8.1f} GWh/yr")
print(f"Site's OWN heating fuel:   steam {site['steam']/1e3:,.0f} + gas {site['gas']/1e3:,.0f} MMBtu/yr "
      f"-> {site['useful_heat_mmbtu']:,.0f} MMBtu useful heat "
      f"({site['useful_heat_mmbtu']/delivered_mmbtu:.0%} of deliverable)")

# monthly shape of the source (flat = good baseload)
monthly = pd.read_csv(DATA / 'site1_nyc' / 'll84_monthly.csv', low_memory=False,
                      usecols=['Calendar Year', 'Month', 'Property Id', 'District Steam Use  (kBtu)',
                               'Natural Gas Use - Monthly (kBtu)', 'Electricity Use  (kBtu)',
                               'Electricity Use (Grid) - Monthly (kBtu)',
                               'Fuel Oil #2 Use - Monthly (kBtu)', 'Fuel Oil #4 Use - Monthly (kBtu)',
                               'Fuel Oil #5&6 Use - Monthly (kBtu)'])
mcols = monthly.columns[3:]
monthly[mcols] = monthly[mcols].apply(pd.to_numeric, errors='coerce')
monthly['elec_kbtu'] = monthly['Electricity Use (Grid) - Monthly (kBtu)'].fillna(monthly['Electricity Use  (kBtu)'])
monthly['heat_fuel_kbtu'] = monthly[['District Steam Use  (kBtu)', 'Natural Gas Use - Monthly (kBtu)',
                                     'Fuel Oil #2 Use - Monthly (kBtu)', 'Fuel Oil #4 Use - Monthly (kBtu)',
                                     'Fuel Oil #5&6 Use - Monthly (kBtu)']].sum(axis=1, min_count=1)
monthly['mon'] = pd.to_datetime(monthly['Month'], format='%y-%b').dt.month
monthly = monthly.drop_duplicates(['Property Id', 'Calendar Year', 'mon'])

src_m = monthly[(monthly['Property Id'] == SITE_ID) & (monthly['Calendar Year'] == 2024)].set_index('mon')
src_mw = src_m['elec_kbtu'] / KBTU_PER_KWH / 1000 / (pd.Series(pd.date_range('2024-01-01', periods=12, freq='MS').days_in_month.values, index=range(1, 13)) * 24)
print(f"2024 monthly avg site power: min {src_mw.min():.1f} MW, max {src_mw.max():.1f} MW, "
      f"max/min = {src_mw.max()/src_mw.min():.2f}  -> near-constant 24/7 source")

# ======================================================================
# 3. Candidate heat off-takers around the site
# ======================================================================
section('2. NEARBY HEAT DEMAND (LL84 buildings, excluding the site)')
near = ll84[(ll84['dist_m'] <= max(RADII_M)) & (ll84['Property ID'] != SITE_ID)].copy()
near['ring'] = pd.cut(near['dist_m'], [0, *RADII_M], labels=[f'<= {r} m' for r in RADII_M])

rings = near.groupby('ring', observed=False).agg(
    buildings=('Property ID', 'count'),
    gfa_mft2=('gfa_ft2', lambda s: s.sum() / 1e6),
    useful_heat_mmbtu=('useful_heat_mmbtu', 'sum'),
    thermal_co2_t=('thermal_co2_t', 'sum'))
rings[['buildings', 'gfa_mft2', 'useful_heat_mmbtu', 'thermal_co2_t']] = rings.cumsum()
rings['cum_heat_vs_supply'] = rings['useful_heat_mmbtu'] / delivered_mmbtu
print('Cumulative by distance (useful heat = fuel x appliance efficiency):')
print(rings.round(2).to_string())

by_type = (near[near['dist_m'] <= 1000].groupby('type')
           .agg(n=('Property ID', 'count'), useful_heat_mmbtu=('useful_heat_mmbtu', 'sum'),
                steam=('steam', 'sum'), gas=('gas', 'sum'),
                oil=('oil2', 'sum'))
           .sort_values('useful_heat_mmbtu', ascending=False))
for c in ['steam', 'gas', 'oil']:
    by_type[c] = (by_type[c] / 1000).round(0)
by_type['useful_heat_mmbtu'] = by_type['useful_heat_mmbtu'].round(0)
by_type['share'] = (by_type['useful_heat_mmbtu'] / by_type['useful_heat_mmbtu'].sum()).round(3)
print('\nHeat demand within 1 km by property type (fuel cols in MMBtu):')
print(by_type.head(12).to_string())

top = (near[near['useful_heat_mmbtu'] > 0]
       .sort_values('useful_heat_mmbtu', ascending=False)
       [['Property Name', 'Address 1', 'type', 'dist_m', 'gfa_ft2', 'Year Built',
         'useful_heat_mmbtu', 'steam', 'gas', 'oil2', 'thermal_co2_t']])
top['main_fuel'] = top[['steam', 'gas', 'oil2']].idxmax(axis=1)
top['share_of_supply'] = top['useful_heat_mmbtu'] / delivered_mmbtu
top.to_csv(OUT / 'site1_offtakers_1500m.csv', index=False)
print('\nTop 15 single heat users within 1.5 km:')
print(top.head(15).drop(columns=['steam', 'gas', 'oil2'])
      .assign(dist_m=lambda d: d.dist_m.round(0), useful_heat_mmbtu=lambda d: d.useful_heat_mmbtu.round(0),
              share_of_supply=lambda d: d.share_of_supply.round(2))
      .to_string(index=False, max_colwidth=28))

fuel_mix = near[near['dist_m'] <= 1000][['steam', 'gas', 'oil2', 'oil4', 'oil56', 'dhw_district']].sum() / 1000
print('\nFuel mix of heating within 1 km (MMBtu):')
print((fuel_mix / fuel_mix.sum()).round(3).to_string())

# ======================================================================
# 4. Seasonality: demand vs constant supply, month by month
# ======================================================================
section('3. SEASONALITY (2024 monthly, buildings within 1 km)')
ids_1km = set(near.loc[near['dist_m'] <= 1000, 'Property ID'])
dm = monthly[(monthly['Property Id'].isin(ids_1km)) & (monthly['Calendar Year'] == 2024)]
demand_m = dm.groupby('mon')['heat_fuel_kbtu'].sum() / 1000 * BOILER_EFF   # MMBtu useful (approx)
supply_m = pd.Series(delivered_mmbtu / 12, index=range(1, 13))

# Central Park heating degree days (base 65 F) per month
w = pd.read_csv(DATA / 'weather' / 'nyc_central_park' / '2024.csv', usecols=['DATE', 'TMP'], low_memory=False)
w['t_c'] = pd.to_numeric(w['TMP'].str.split(',').str[0], errors='coerce') / 10
w = w[w['t_c'].between(-40, 50)]
w['date'] = pd.to_datetime(w['DATE']).dt.date
daily_f = w.groupby('date')['t_c'].mean() * 9 / 5 + 32
hdd = (65 - daily_f).clip(lower=0)
hdd_m = hdd.groupby(pd.to_datetime(hdd.index).month).sum()

season = pd.DataFrame({'HDD65': hdd_m.round(0), 'demand_mmbtu': demand_m.round(0),
                       'supply_mmbtu': supply_m.round(0)})
season['supply_covers'] = (season['supply_mmbtu'] / season['demand_mmbtu']).round(2)
print(f"Monthly LL84 data found for {dm['Property Id'].nunique()} of {len(ids_1km)} buildings within 1 km")
print(season.to_string())

# Split demand into a year-round base (hot water, process) and weather-driven space heat
b, a = np.polyfit(season['HDD65'], season['demand_mmbtu'], 1)
base_share = a * 12 / season['demand_mmbtu'].sum()
print(f"\nRegression demand = {a:,.0f} + {b:,.1f} x HDD  ->  year-round base load ~{base_share:.0%} of annual demand")
print(f"Base load ~{a*12:,.0f} MMBtu/yr vs deliverable {delivered_mmbtu:,.0f}: "
      f"constant DC heat matches the base load, winter peak needs storage/backup")
season.to_csv(OUT / 'site1_seasonality.csv')

# ======================================================================
# 5. Economics: buyer's current cost X vs heat-pump cost
# ======================================================================
section("4. PRICE CHECK: buyer's cost X vs cost of upgraded DC heat (2021 $, NYS SEDS)")
prices = pd.read_csv(DATA / 'prices' / 'nys_energy_prices_usd_per_mmbtu.csv')
p = prices[prices['Year'] == prices['Year'].max()].set_index('Sector')
yr = prices['Year'].max()
elec = p.loc['Commercial', 'Electricity']
rows = {
    'Commercial gas boiler': (p.loc['Commercial', 'Natural Gas'] / BOILER_EFF, EF['gas'] * 1000 / BOILER_EFF),
    'Residential gas boiler': (p.loc['Residential', 'Natural Gas'] / BOILER_EFF, EF['gas'] * 1000 / BOILER_EFF),
    'Commercial #2 oil boiler': (p.loc['Commercial', 'Distillate'] / BOILER_EFF, EF['oil2'] * 1000 / BOILER_EFF),
    'Electric resistance': (elec, EF['elec_kwh'] * 1e6 / 3412 / 1),
    f'DC heat via heat pump (COP {HP_COP})': (elec / HP_COP, EF['elec_kwh'] * 1e6 / 3412 / HP_COP),
}
econ = pd.DataFrame(rows, index=['energy_$_per_mmbtu_heat', 'tco2_per_mmbtu_heat']).T
econ['ll97_$_per_mmbtu_if_over'] = econ['tco2_per_mmbtu_heat'] * LL97_PENALTY
econ['all_in_$_per_mmbtu'] = econ['energy_$_per_mmbtu_heat'] + econ['ll97_$_per_mmbtu_if_over']
print(f"Price year {yr}. Con Ed steam price is NOT in this file - add it by hand.")
print(econ.round(3).to_string())
econ.to_csv(OUT / 'site1_heat_cost_comparison.csv')

# ======================================================================
# 6. Carbon
# ======================================================================
section('5. CARBON (if all deliverable heat displaced gas/steam mix within 1 km)')
mix = fuel_mix[['steam', 'gas', 'oil2', 'oil4', 'oil56']]
mix = mix / mix.sum()
avoided_t_per_mmbtu = sum(mix[f] * EF[f] * 1000 / (STEAM_EFF if f == 'steam' else BOILER_EFF) for f in mix.index)
avoided = delivered_mmbtu * avoided_t_per_mmbtu
added = hp_elec_mwh * 1000 * EF['elec_kwh']
print(f"Displaced fuel emissions   {avoided:10,.0f} tCO2e/yr")
print(f"Heat-pump grid emissions   {added:10,.0f} tCO2e/yr (LL97 2024-29 grid factor)")
print(f"Net reduction              {avoided-added:10,.0f} tCO2e/yr  (~${(avoided-added)*LL97_PENALTY/1e6:,.1f}M/yr of LL97 penalty exposure)")

# NYISO fossil share by month (heat-pump electricity is dirtier in winter?)
frames = []
for z in sorted(glob.glob(str(DATA / 'grid' / 'nyiso_fuel_mix' / '2024*_rtfuelmix.zip'))):
    with zipfile.ZipFile(z) as zf:
        for name in zf.namelist():
            if name.endswith('.csv'):
                frames.append(pd.read_csv(zf.open(name), encoding='latin-1'))
fm = pd.concat(frames)
fm['mon'] = pd.to_datetime(fm['Time Stamp'], format='%m/%d/%Y %H:%M:%S').dt.month
fm['fossil'] = fm['Fuel Category'].isin(['Dual Fuel', 'Natural Gas', 'Other Fossil Fuels'])
g = fm.groupby(['mon', 'fossil'])['Gen MW'].sum().unstack()
print('\nNYISO 2024 fossil share of generation by month:')
print((g[True] / g.sum(axis=1)).round(2).to_string())

# ======================================================================
# 7. Equity / community
# ======================================================================
section('6. COMMUNITY CONTEXT')
import geopandas as gpd
from shapely import wkt
from shapely.geometry import Point

dac = pd.read_csv(DATA / 'equity' / 'nys_dac_2023.csv', low_memory=False)
dac = gpd.GeoDataFrame(dac, geometry=dac['the_geom'].apply(wkt.loads), crs='EPSG:4326').to_crs(32618)
site_pt = gpd.GeoSeries([Point(SITE_LON, SITE_LAT)], crs='EPSG:4326').to_crs(32618).iloc[0]
dac['dist_m'] = dac.distance(site_pt)
d15 = dac[dac['dist_m'] <= 1500]
print(f"Census tracts within 1.5 km: {len(d15)}, designated DAC: {(d15['DAC_Designation'] == 'Designated as DAC').sum()}")
cols = ['GEOID', 'DAC_Designation', 'dist_m', 'Population_Count', 'LMI_80_AMI',
        'Homes_Built_Before_1960', 'Renter_Percent', 'Asthma_ED_Rate']
print(d15.sort_values('dist_m')[cols].head(15).round(2).to_string(index=False))
d15.drop(columns=['geometry', 'the_geom']).to_csv(OUT / 'site1_dac_tracts_1500m.csv', index=False)

nycha = pd.read_csv(DATA / 'site1_nyc' / 'nycha_development_data_book.csv')
chelsea = nycha[(nycha['BOROUGH'].str.upper() == 'MANHATTAN') & (nycha['COMMUNITY DISTIRCT'].astype(str).str.strip() == '4')]
print('\nNYCHA developments in Manhattan Community District 4 (Chelsea/Clinton):')
print(chelsea[['DEVELOPMENT', 'TOTAL NUMBER OF APARTMENTS', 'TOTAL POPULATION', 'NUMBER OF STORIES',
               'LOCATION STREET A', 'LOCATION STREET B', 'COMPLETION DATE']].to_string(index=False))

near_res = near[(near['dist_m'] <= 1000) & near['type'].str.contains('Multifamily|Residence', na=False)]
print(f"\nLL84 multifamily buildings within 1 km: {len(near_res)}, "
      f"useful heat {near_res['useful_heat_mmbtu'].sum():,.0f} MMBtu/yr")
ll84_nycha = near[near['Property Name'].str.contains('NYCHA|Fulton|Elliott|Chelsea Houses', case=False, na=False)]
print('LL84 records that look like NYCHA within 1.5 km:')
print(ll84_nycha[['Property Name', 'Address 1', 'dist_m', 'gfa_ft2', 'useful_heat_mmbtu', 'steam', 'gas']]
      .round(0).to_string(index=False))

schools = pd.read_csv(DATA / 'site1_nyc' / 'school_locations.csv', low_memory=False)
schools['dist_m'] = haversine_m(SITE_LAT, SITE_LON, pd.to_numeric(schools['LATITUDE'], errors='coerce'),
                                pd.to_numeric(schools['LONGITUDE'], errors='coerce'))
print(f"\nPublic schools within 500 m: {(schools['dist_m'] <= 500).sum()}, within 1 km: {(schools['dist_m'] <= 1000).sum()}")

print(f'\nTables written to {OUT}')
