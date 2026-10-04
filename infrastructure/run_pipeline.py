"""Run: python -m infrastructure.run_pipeline [--refresh] [--osm]"""
import argparse
import json
import math
import subprocess
from collections import Counter
from pathlib import Path
from .config import ROOT,read_site
from .distance import haversine_m
from .http import Client
from .discover_nyc import locate
from .discover_osm import discover
from .normalize import normalize,deduplicate
from .export import export,csv_write

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--refresh',action='store_true',help='Retrieve fresh location API responses; otherwise use committed snapshots')
    p.add_argument('--osm',action='store_true',help='Retrieve mapped leads separately for review; never automatically promote them')
    p.add_argument('--out',type=Path,default=ROOT/'data/infrastructure')
    args=p.parse_args();out=args.out;out.mkdir(parents=True,exist_ok=True)
    inputs=ROOT/'data/infrastructure/inputs'
    site=read_site();client=Client(ROOT/'data/infrastructure/raw',args.refresh)
    assets=json.loads((inputs/'reviewed_assets.json').read_text());rows=[];excluded=[]
    for asset in assets:
        try:
            loc={} if asset.get('location_scope')=='service_territory_not_point' else locate(asset,client)
            row=normalize(asset,loc,site)
            if row['distance_ring'] is None:
                excluded.append(dict(asset_id=asset['asset_id'],reason='outside_maximum_ring',distance_m=row['distance_m']));continue
            rows.append(row)
        except Exception as e:excluded.append(dict(asset_id=asset['asset_id'],reason='location_or_validation_failure',error=str(e)))
    if args.osm:
        try:
            (out/'osm_review_queue.json').write_text(json.dumps(discover(site,client),indent=2))
        except Exception as e:excluded.append(dict(asset_id='osm-discovery',reason='optional_api_failure',error=str(e)))
    rows=deduplicate(rows)
    if not rows:raise RuntimeError('No candidates could be exported')
    rows.sort(key=lambda r:(r['distance_m'] is None,r['distance_m'] or 0,r['asset_id']))
    # Independent spherical law of cosines check against the haversine implementation.
    for r in rows:
        if r['latitude'] is None:continue
        a,b=map(math.radians,(site['latitude'],r['latitude']))
        dlon=math.radians(r['longitude']-site['longitude'])
        check=6371000*math.acos(max(-1,min(1,math.sin(a)*math.sin(b)+math.cos(a)*math.cos(b)*math.cos(dlon))))
        if abs(check-r['distance_m'])>0.02:raise ValueError('Independent distance validation failed')
    export(rows,out)
    methods=json.loads((inputs/'transport_methods.json').read_text())
    csv_write(out/'transport_methods.csv',methods,list(methods[0]))
    discrepancy=haversine_m(site['latitude'],site['longitude'],site['simulator_coordinate_reference']['latitude'],site['simulator_coordinate_reference']['longitude'])
    summary=dict(site=site,simulator_coordinate_difference_m=round(discrepancy,3),
        candidate_count=len(rows),geolocated_count=sum(r['latitude'] is not None for r in rows),
        by_role=dict(Counter(r['role'] for r in rows)),closest_10=[{'asset_name':r['asset_name'],'distance_m':r['distance_m'],'distance_ring':r['distance_ring']} for r in rows if r['distance_m'] is not None][:10],
        exclusions=excluded,api_events=client.events,site2='not_run_deferred_by_user',
        validation=dict(unique_asset_ids=True,coordinate_bounds_checked=True,independent_distances_checked=True,
                        every_row_has_evidence=True,unsupported_engineering_values_null=True),
        last_evidence_review='2026-10-03',note='Location refresh does not update evidence review dates. Service territory is not a point or verified tie-in. Campus points are approximate references.')
    (out/'validation_summary.json').write_text(json.dumps(summary,indent=2))
    sources=['# Infrastructure Sources and Limitations','## Scope',
        f'Site 1 only. Demand-model center: {site["latitude"]}, {site["longitude"]}, read from combine_site1.py. Site ID: {site["site_id"]}. Rings: {site["rings_m"]} m.',
        f'The engine site YAML uses a different center ({discrepancy:.1f} m away). It was not modified. Distances here follow the demand model. Rings are smallest enclosing thresholds, not five duplicate records.',
        'Evidence was reviewed on 2026-10-03. Replaying snapshots does not reverify operating status. Point locations come from official PLUTO parcel data or Census address-range interpolation; campus reference points are not equipment coordinates.',
        'Capacity means usable thermal capacity in MW_th. No electrical capacity is relabeled as thermal. Unknown CSV values are blank; GeoJSON uses null. Connection availability is unknown for every asset.',
        'The proposed TEN and its source building are distinct, related records, not independent available heat sources. Hudson Yards is recorded once as a campus system rather than inventing locations for its two plants.',
        '## Asset Evidence']
    for a in assets:
        sources += [f'### {a["asset_name"]}',f'- Source: {a["source_url"]}',f'- Evidence: {a["evidence_note"]}']
        sources += [f'- Additional source: {u}' for u in a.get('additional_source_urls',[])]
    sources += ['## Search Coverage and Negative Findings',
        '- Existing steam: Con Edison confirms its Manhattan network. No public surveyed nearby tie-in geometry or available injection capacity was established. The service-area record therefore has null geometry/distance and is excluded from closest-asset ranking.',
        '- Central plants and institutions: NYU, Penn South, Hudson Yards and FIT have documented thermal equipment. Campus location and external surplus capacity need engineering confirmation.',
        '- Wastewater/sewer: NYC DEP wastewater-system documentation and Canal Street pumping-station records were investigated. Canal Street station existence is supported, but an exact facility coordinate was not verified here; it is not turned into a geolocated candidate. North River WRRF is outside the local neighborhood; a sewer connection is not inferred from drainage-area coverage.',
        '  - https://www.nyc.gov/site/dep/water/wastewater-treatment-plants.page',
        '  - https://a856-cityrecord.nyc.gov/RequestDetail/20231013105',
        '  - https://s-media.nyc.gov/agencies/lpc/arch_reports/1798.pdf',
        '- Other waste heat: operator-confirmed JFK13 was investigated but excluded because its geocoded reference point is beyond 2,200 m. No heat export arrangement is established.',
        '- Storage and geothermal: no separately verified local installed thermal-storage or geoexchange asset was established. Method options are not claimed as existing assets.',
        '- EPA ECHO/FRS/GHGRP and EIA bulk inventories were not ingested in this release; no completeness claim is made for those inventories. OpenStreetMap, when requested, is saved as an unreviewed queue and excluded from approved candidates.',
        '- Official location data: NYC Open Data PLUTO API and US Census geocoder; exact request URLs, response hashes and retrieval timestamps are preserved in raw/*.json and validation_summary.json.',
        '- During initial endpoint investigation, Python urllib failed certificate verification. The pipeline uses system curl with certificate verification enabled. An initial free-text Census query using Manhattan returned Kansas matches; these were rejected. The production adapter uses separate New York/NY/ZIP fields and rejects out-of-state or mismatched-ZIP results.',
        '- The optional OSM query returned zero elements in the initial live run. This describes the queried tags only, not evidence that local infrastructure is absent. The NYU sustainability blog failed extraction in the web reader; official NYC environmental-review documentation independently supports the plant/address.',
        '## Transport Methods',
        'Six qualitative pathways are design categories, not selected solutions or cost estimates. Method flags are conditional engineering requirements, not proof of available networks.',
        '- https://www.coned.com/en/our-energy-future/our-energy-vision/where-we-are-going/thermal-energy-networks',
        '- https://www.coned.com/en/commercial-industrial/steam',
        '## Exclusions and API Results',json.dumps(excluded,indent=2),
        'See validation_summary.json for every location/API request and its outcome. A failed optional discovery does not invalidate reviewed source records.',
        '## Regeneration','```sh\npython -m infrastructure.run_pipeline\n# Refresh location snapshots and optionally investigate OSM:\npython -m infrastructure.run_pipeline --refresh --osm\n```',
        'Python 3.11+ and curl are sufficient. Run from repository root. Offline regeneration requires the included response snapshots. No simulator, ML provider, optimizer, routing or physics code is invoked.']
    (out/'SOURCES.md').write_text('\n\n'.join(sources))
    print(json.dumps({k:v for k,v in summary.items() if k not in ('api_events',)},indent=2))
    if any(e['reason']=='location_or_validation_failure' for e in excluded):return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
