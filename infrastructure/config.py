"""Read literal demand-model constants without importing its executable analysis."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read_site(root=ROOT):
    path = root / 'combine_site1.py'
    values = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Tuple) and [getattr(x,'id',None) for x in target.elts] == ['SITE_LAT','SITE_LON']:
                    values['latitude'],values['longitude'] = ast.literal_eval(node.value)
                elif isinstance(target,ast.Name) and target.id in ('RINGS_M','SITE_ID'):
                    values[target.id] = ast.literal_eval(node.value)
    if not all(k in values for k in ('latitude','longitude','RINGS_M','SITE_ID')):
        raise ValueError('Missing literal Site 1 coordinates/rings in combine_site1.py; refusing to guess')
    cfg=(root/'engine/sites/chelsea.yaml').read_text()
    site_id=re.search(r'^  id: (\S+)',cfg,re.M).group(1)
    engine_lat=float(re.search(r'^  lat: ([-.\d]+)',cfg,re.M).group(1))
    engine_lon=float(re.search(r'^  lon: ([-.\d]+)',cfg,re.M).group(1))
    return dict(site_id=site_id,name='111 8th Avenue',latitude=values['latitude'],longitude=values['longitude'],
                rings_m=values['RINGS_M'],ll84_property_id=values['SITE_ID'],coordinate_source='combine_site1.py',
                simulator_coordinate_reference=dict(latitude=engine_lat,longitude=engine_lon),
                site2_status='deferred_by_user')
