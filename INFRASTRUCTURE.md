# Site 1 Infrastructure Discovery

This additive module discovers and documents physical thermal assets and potential anchor users. It does not import or modify the demand/supply models, simulator, dispatch, optimizer, routing, storage control, or building-selection logic. Site 2 is deferred by user instruction.

## Run from the repository root

```sh
# Deterministic candidate exports from the included location-response snapshots:
python -m infrastructure.run_pipeline

# Refresh public location data and collect optional OSM leads:
python -m infrastructure.run_pipeline --refresh --osm

# Offline tests:
python -m unittest discover -s tests -p test_infrastructure.py -v
```

Python 3.11+ is sufficient. The refresh command also requires `curl` on PATH and internet access; no new Python packages are required. Offline regeneration makes no network requests. Run from the repository root, not from the data directory.

If receiving this module as an overlay ZIP, extract it into a clone of `myramay/data-center-heatos`, preserving the `infrastructure/`, `data/infrastructure/`, and `tests/` paths. It requires the existing `combine_site1.py` and `engine/sites/chelsea.yaml` files. No existing teammate modules need replacement.

## Coordinates and rings

The module parses literal constants in `combine_site1.py` without importing or executing it:

- Latitude: 40.740704; longitude: -74.001844.
- Rings: 250, 500, 1000, 1500, 2200 m.
- Site ID: `chelsea`, reused from the engine site YAML.
- LL84 Property ID: 7536925, preserved as metadata.

The simulator YAML has a different center (40.7411, -74.0032), approximately 122 m away. This module follows the demand/off-taker coordinates and records the discrepancy. It does not silently change either existing configuration.

`distance_ring` is the smallest inclusive upper threshold containing an asset. Use `distance_m <= radius` for cumulative radius comparisons. Distances are straight-line haversine distances with the same 6,371,000 m Earth radius as the demand script. They are not pipe lengths or access routes.

## Outputs

- `data/infrastructure/infrastructure_candidates.csv`
- `data/infrastructure/infrastructure_candidates.geojson`
- `data/infrastructure/transport_methods.csv`
- `data/infrastructure/SOURCES.md`
- `data/infrastructure/validation_summary.json`
- `data/infrastructure/osm_review_queue.json`, when `--osm` succeeds

The initial run contains 11 records: 10 geolocated asset/project records and one steam-network service-area context record with null geometry and distance. The TEN project and its source building are explicitly related records, not two independent heat resources. Four roles describe physical opportunities; the existing steam-network row is geographic context, not a surveyed connection point.

CSV unknowns are empty; GeoJSON unknowns are JSON null. `capacity_MW_if_known` is usable thermal capacity (MW_th), not electrical plant capacity. All capacities and temperatures in this initial release remain unknown. Connection availability is unknown throughout. `confidence` describes evidence quality, not likelihood of technical success or spare capacity. Initial records use MEDIUM because thermal access is inferred, project status is proposed, or equipment coordinates are not verified.

Coordinates are official PLUTO parcel representative points or Census TIGER address-range interpolations. Campus-system points refer to a documented campus building, not an equipment room or pipe interface. This precision is appropriate for preliminary screening only.

## Evidence and reproducibility

`inputs/reviewed_assets.json` contains manually reviewed government, utility and operator evidence. This review is intentional: a map tag or an institutional address alone cannot establish available heat or a connection. `last_verified` is the date the source evidence was reviewed. Refreshing coordinates does not silently update this date or certify that an older operating statement remains current.

`raw/*.json` preserves actual location API responses with request URL, retrieval timestamp and SHA-256 response hash. Replay uses these snapshots. `--refresh` retrieves new location data. `discover_osm.py` stores optional mapped leads separately; they require review and are not promoted automatically. The narrow initial OSM query returned zero elements.

Source discovery is deliberately evidence-led, not an exhaustive facility census. EPA ECHO/FRS/GHGRP and EIA bulk data were not ingested. Wastewater pumping-station sources were investigated, but exact local equipment coordinates and recoverable heat were not verified; no sewer-heat candidate is invented. JFK13 at 32 Avenue of the Americas was excluded at approximately 2,330 m. All exclusions and API failures are logged.

## Integration for teammates

MYRA can read type, role, known temperature/capacity, existing/proposed status, and distance. MIRAYA can read coordinates, confidence, existing-network flags, connection status and candidate pathway IDs. Neither should treat a `candidate_transport_types` entry as a feasible selected connection.

Filter out `location_scope=service_territory_not_point` before point-based routing. Campus reference points require engineering location refinement. A proposed network is not operational infrastructure. The six transport-method rows are qualitative pathway definitions; the storage-assisted method is an overlay, not an installed storage asset or a mutually exclusive option.

## Validation

The pipeline prints role counts and the ten closest geolocated records. It checks coordinate bounds, evidence presence, duplicate IDs/facilities, unsupported numeric fields, and distances against an independent spherical-law-of-cosines calculation. Tests cover ring boundaries, zero distance, wrong-state geocoding, null geometry, duplicate conflicts, missing evidence and unsupported capacity claims. No unrelated simulator tests or model behavior are changed.

Code organization:

```text
infrastructure/config.py          demand-coordinate and site-ID adapters
infrastructure/http.py            API requests and response snapshots
infrastructure/discover_nyc.py    NYC PLUTO and Census location adapters
infrastructure/discover_osm.py    optional OSM leads for review
infrastructure/normalize.py       taxonomy, numeric evidence and deduplication
infrastructure/distance.py        coordinate checks and ring assignment
infrastructure/export.py          CSV and GeoJSON serialization
infrastructure/run_pipeline.py    orchestration, reporting and source registry
```
