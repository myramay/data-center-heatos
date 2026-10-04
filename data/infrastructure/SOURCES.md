# Infrastructure Sources and Limitations

## Scope

Site 1 only. Demand-model center: 40.740704, -74.001844, read from combine_site1.py. Site ID: chelsea. Rings: [250, 500, 1000, 1500, 2200] m.

The engine site YAML uses a different center (122.4 m away). It was not modified. Distances here follow the demand model. Rings are smallest enclosing thresholds, not five duplicate records.

Evidence was reviewed on 2026-10-03. Replaying snapshots does not reverify operating status. Point locations come from official PLUTO parcel data or Census address-range interpolation; campus reference points are not equipment coordinates.

Capacity means usable thermal capacity in MW_th. No electrical capacity is relabeled as thermal. Unknown CSV values are blank; GeoJSON uses null. Connection availability is unknown for every asset.

The proposed TEN and its source building are distinct, related records, not independent available heat sources. Hudson Yards is recorded once as a campus system rather than inventing locations for its two plants.

## Asset Evidence

### 85 10th Avenue - documented pilot heat-source building

- Source: https://www.coned.com/en/our-energy-future/our-energy-vision/where-we-are-going/thermal-energy-networks/chelsea

- Evidence: Con Edison identifies this existing office building as the excess-heat source for its proposed Chelsea TEN. Heat recovery and third-party connection are not confirmed operational.

### Fulton Houses - 401 West 16th Street

- Source: https://www.coned.com/en/our-energy-future/our-energy-vision/where-we-are-going/thermal-energy-networks/chelsea

- Evidence: Named by Con Edison as a proposed Chelsea TEN heat recipient. Existing housing; pilot connection remains proposed.

### Fulton Houses - 410 West 17th Street

- Source: https://www.coned.com/en/our-energy-future/our-energy-vision/where-we-are-going/thermal-energy-networks/chelsea

- Evidence: Named by Con Edison as a proposed Chelsea TEN heat recipient. Existing housing; pilot connection remains proposed.

### Fulton Houses - 420 West 17th Street

- Source: https://www.coned.com/en/our-energy-future/our-energy-vision/where-we-are-going/thermal-energy-networks/chelsea

- Evidence: Named by Con Edison as a proposed Chelsea TEN heat recipient. Existing housing; pilot connection remains proposed.

### Con Edison Chelsea TEN pilot

- Source: https://www.coned.com/en/our-energy-future/our-energy-vision/where-we-are-going/thermal-energy-networks/chelsea

- Evidence: Proposed network linking the documented source building and three Fulton Houses buildings. Point marks the source address, not a surveyed route or connection.

### NYU Mercer Street cogeneration plant

- Source: https://www.nyc.gov/assets/planning/download/pdf/applicants/env-review/nyu_core/01_feis.pdf

- Evidence: NYC environmental review identifies the below-grade cogeneration plant at 251 Mercer Street. NYU describes campus heating/cooling service. External export capacity and current connection availability are unknown.

- Additional source: https://wp.nyu.edu/sustainability-nyusustainablog/2021/04/13/green-campus-tour/

### Penn South campus cogeneration system

- Source: https://www.nyserda.ny.gov/All-Programs/Multifamily-Buildings-of-Excellence/Early-Design-Support-Program/MRH-Penn-South

- Evidence: NYSERDA confirms campus cogeneration. Location is a documented campus building, not the powerhouse itself. Operator annual reports also describe the powerhouse; available surplus heat is unknown.

- Additional source: https://www.pennsouth.coop/campus-maps.html

- Additional source: https://www.pennsouth.coop/uploads/8/2/7/5/82753010/ps-annual-reports-2025.pdf

### FIT campus electric chiller plant

- Source: https://news.fitnyc.edu/2024/04/29/fit-invests-in-energy-efficient-infrastructure/

- Evidence: FIT reports replacing its steam-powered chiller with an electric chiller in 2024. Campus cooling infrastructure may offer heat-recovery integration; that capability is inferred, not documented. Point is the campus mailing address.

- Additional source: https://www.fitnyc.edu/life-at-fit/campus/directions.php

### Chelsea Recreation Center indoor pool

- Source: https://nycgovparks.org/facilities/recreationcenters/M260

- Evidence: NYC Parks lists this recreation center and pool. Pool and hot-water loads motivate anchor screening; heat demand, current equipment and tie-in feasibility are unverified.

### Hudson Yards cogeneration and thermal reuse system

- Source: https://www.hudsonyardsnewyork.com/stories/sustainability-big-scale

- Evidence: The operator documents two cogeneration plants with heat reused for neighborhood heating and cooling. Recorded once as a campus system. Point is 10 Hudson Yards, not either plant or a connection point.

- Additional source: https://www.energystar.gov/buildings/certified_buildings_and_plants/b_6070237

### Digital Realty JFK13 - 32 Avenue of the Americas

- Source: https://www.digitalrealty.com/data-centers/americas/new-york/jfk13

- Evidence: Operator confirms a data center at this address. Waste heat is a potential resource; a recovery system, usable thermal output and external access are not publicly established here.

### Con Edison Manhattan steam network - service-area context

- Source: https://www.coned.com/en/commercial-industrial/steam

- Evidence: Utility confirms an existing Manhattan steam system. Public service-area context does not locate a main, tie-in point or available export capacity near 111 8th Avenue. No point geometry or distance is assigned.

## Search Coverage and Negative Findings

- Existing steam: Con Edison confirms its Manhattan network. No public surveyed nearby tie-in geometry or available injection capacity was established. The service-area record therefore has null geometry/distance and is excluded from closest-asset ranking.

- Central plants and institutions: NYU, Penn South, Hudson Yards and FIT have documented thermal equipment. Campus location and external surplus capacity need engineering confirmation.

- Wastewater/sewer: NYC DEP wastewater-system documentation and Canal Street pumping-station records were investigated. Canal Street station existence is supported, but an exact facility coordinate was not verified here; it is not turned into a geolocated candidate. North River WRRF is outside the local neighborhood; a sewer connection is not inferred from drainage-area coverage.

  - https://www.nyc.gov/site/dep/water/wastewater-treatment-plants.page

  - https://a856-cityrecord.nyc.gov/RequestDetail/20231013105

  - https://s-media.nyc.gov/agencies/lpc/arch_reports/1798.pdf

- Other waste heat: operator-confirmed JFK13 was investigated but excluded because its geocoded reference point is beyond 2,200 m. No heat export arrangement is established.

- Storage and geothermal: no separately verified local installed thermal-storage or geoexchange asset was established. Method options are not claimed as existing assets.

- EPA ECHO/FRS/GHGRP and EIA bulk inventories were not ingested in this release; no completeness claim is made for those inventories. OpenStreetMap, when requested, is saved as an unreviewed queue and excluded from approved candidates.

- Official location data: NYC Open Data PLUTO API and US Census geocoder; exact request URLs, response hashes and retrieval timestamps are preserved in raw/*.json and validation_summary.json.

- During initial endpoint investigation, Python urllib failed certificate verification. The pipeline uses system curl with certificate verification enabled. An initial free-text Census query using Manhattan returned Kansas matches; these were rejected. The production adapter uses separate New York/NY/ZIP fields and rejects out-of-state or mismatched-ZIP results.

- The optional OSM query returned zero elements in the initial live run. This describes the queried tags only, not evidence that local infrastructure is absent. The NYU sustainability blog failed extraction in the web reader; official NYC environmental-review documentation independently supports the plant/address.

## Transport Methods

Six qualitative pathways are design categories, not selected solutions or cost estimates. Method flags are conditional engineering requirements, not proof of available networks.

- https://www.coned.com/en/our-energy-future/our-energy-vision/where-we-are-going/thermal-energy-networks

- https://www.coned.com/en/commercial-industrial/steam

## Exclusions and API Results

[
  {
    "asset_id": "jfk13-32-americas",
    "reason": "outside_maximum_ring",
    "distance_m": 2330.035
  }
]

See validation_summary.json for every location/API request and its outcome. A failed optional discovery does not invalidate reviewed source records.

## Regeneration

```sh
python -m infrastructure.run_pipeline
# Refresh location snapshots and optionally investigate OSM:
python -m infrastructure.run_pipeline --refresh --osm
```

Python 3.11+ and curl are sufficient. Run from repository root. Offline regeneration requires the included response snapshots. No simulator, ML provider, optimizer, routing or physics code is invoked.