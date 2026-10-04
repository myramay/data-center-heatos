import csv
import json
from .normalize import FIELDS

def csv_write(path,rows,fields):
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for r in rows:w.writerow({k:json.dumps(v) if isinstance(v,(list,dict)) else v for k,v in r.items()})

def export(rows,out):
    out.mkdir(parents=True,exist_ok=True)
    csv_write(out/'infrastructure_candidates.csv',rows,FIELDS)
    features=[dict(type='Feature',id=r['asset_id'],geometry=None if r['latitude'] is None else dict(type='Point',coordinates=[r['longitude'],r['latitude']]),properties=r) for r in rows]
    (out/'infrastructure_candidates.geojson').write_text(json.dumps(dict(type='FeatureCollection',features=features),indent=2,allow_nan=False))
