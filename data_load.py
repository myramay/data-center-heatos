from pathlib import Path

import pandas as pd
import numpy as np

LL84_PATH = (Path(__file__).parent / 'heat-reuse-data' / 'data' / 'site1_nyc'
             / 'll84_annual_2022_present.csv')

# LL84 has 265 columns; keep only what's needed for heat-reuse siting
LL84_COLS = [
    # identity / location
    'Calendar Year',
    'Property ID',
    'Parent Property ID',
    'Property Name',
    'NYC Borough, Block and Lot (BBL)',
    'NYC Building Identification Number (BIN)',
    'Address 1',
    'Postal Code',
    'Borough',
    'Latitude',
    'Longitude',
    # building profile
    'Primary Property Type - Self Selected',
    'Largest Property Use Type',
    'Property GFA - Calculated (Buildings) (ft²)',
    'Data Center - Gross Floor Area (ft²)',
    'Year Built',
    # energy
    'Site EUI (kBtu/ft²)',
    'Site Energy Use (kBtu)',
    'Electricity Use - Grid Purchase (kWh)',
    'Natural Gas Use (kBtu)',
    'District Steam Use (kBtu)',
    'District Hot Water Use (kBtu)',
    'Fuel Oil #2 Use (kBtu)',
    'Fuel Oil #4 Use (kBtu)',
    'Fuel Oil #5 & 6 Use (kBtu)',
    # emissions
    'Total (Location-Based) GHG Emissions (Metric Tons CO2e)',
]

HEAT_COLS = [
    'Natural Gas Use (kBtu)',
    'District Steam Use (kBtu)',
    'District Hot Water Use (kBtu)',
    'Fuel Oil #2 Use (kBtu)',
    'Fuel Oil #4 Use (kBtu)',
    'Fuel Oil #5 & 6 Use (kBtu)',
]
GFA = 'Property GFA - Calculated (Buildings) (ft²)'
# heating fuel per ft2 above this is a campus/central-plant meter, not one building
MAX_HEAT_KBTU_PER_FT2 = 400


def load_ll84(path=LL84_PATH):
    """LL84 annual benchmarking, one row per building or campus, no double counting."""
    places = pd.read_csv(path, usecols=LL84_COLS, low_memory=False)

    # "Not Available" etc. -> NaN so the energy columns are numbers
    num_cols = ['Latitude', 'Longitude', GFA, 'Year Built', 'Site EUI (kBtu/ft²)',
                'Site Energy Use (kBtu)', 'Electricity Use - Grid Purchase (kWh)',
                'Data Center - Gross Floor Area (ft²)',
                'Total (Location-Based) GHG Emissions (Metric Tons CO2e)', *HEAT_COLS]
    places[num_cols] = places[num_cols].apply(pd.to_numeric, errors='coerce')

    # one row per building: most recent year it reported
    places = (places.drop_duplicates()
                    .sort_values('Calendar Year')
                    .drop_duplicates('Property ID', keep='last'))

    places['heat_fuel_kbtu'] = places[HEAT_COLS].fillna(0).sum(axis=1)

    # Campuses are reported twice: a parent record (whole campus) plus child
    # buildings, and the campus plant is often metered on one child. Treat each
    # campus as ONE off-taker: if the parent has heating data keep it and drop the
    # children; if the parent is empty keep the children and drop the parent.
    parent_id = pd.to_numeric(places['Parent Property ID'], errors='coerce')
    is_child = parent_id.notna() & (parent_id != places['Property ID'])
    parent_ids = set(parent_id[is_child].astype(int))
    heat_by_id = places.set_index('Property ID')['heat_fuel_kbtu']
    parents_with_heat = {p for p in parent_ids if heat_by_id.get(p, 0) > 0}
    drop_child = is_child & parent_id.isin(parents_with_heat)
    drop_parent = (places['Property ID'].isin(parent_ids)
                   & ~places['Property ID'].isin(parents_with_heat))
    places = places[~drop_child & ~drop_parent]

    # Duplicate submissions ("Copy of ...")
    places = places[~places['Property Name'].str.startswith('Copy of', na=False)]

    # Same address, or same tax lot, with identical heating fuel = one meter reported twice
    has_heat = places['heat_fuel_kbtu'] > 0
    places = pd.concat([
        places[~has_heat],
        places[has_heat]
        .drop_duplicates(['Address 1', 'heat_fuel_kbtu'])
        .drop_duplicates(['NYC Borough, Block and Lot (BBL)', 'heat_fuel_kbtu']),
    ])

    # Impossible intensities = a central plant billed to one building
    places['heat_kbtu_per_ft2'] = places['heat_fuel_kbtu'] / places[GFA]
    places = places[~(places['heat_kbtu_per_ft2'] > MAX_HEAT_KBTU_PER_FT2)]
    return places


if __name__ == '__main__':
    places = load_ll84()
    print(places.shape)
    print(places.head())
