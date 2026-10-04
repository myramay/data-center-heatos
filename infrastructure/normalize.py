from .distance import haversine_m,ring,valid_coordinate

ROLES={'existing_heat_network','third_party_heat_source','thermal_plant','thermal_storage','utility_infrastructure','potential_anchor_sink'}
FIELDS=['site_id','asset_id','asset_name','asset_type','role','latitude','longitude','distance_m','distance_ring',
        'technology','existing_or_proposed','temperature_C_if_known','capacity_MW_if_known','operator_if_known',
        'source_name','source_url','evidence_note','confidence','last_verified',
        'coordinate_method','coordinate_source_url','coordinate_record_id','location_scope',
        'existing_network_flag','connection_availability','candidate_transport_types','related_asset_ids','capacity_basis']

def normalize(asset,loc,site):
    row={k:asset.get(k) for k in FIELDS};row.update(loc);row['site_id']=site['site_id']
    if row['role'] not in ROLES:raise ValueError('Invalid role')
    if row['confidence'] not in ('HIGH','MEDIUM','LOW'):raise ValueError('Invalid confidence')
    if not row['source_url'] or not row['evidence_note']:raise ValueError('Missing evidence')
    if row['location_scope']=='service_territory_not_point':
        if row['latitude'] is not None or row['longitude'] is not None:raise ValueError('Service context must not invent a point')
        row['distance_m']=None;row['distance_ring']='unknown'
    else:
        if not valid_coordinate(row['latitude'],row['longitude']):raise ValueError('Impossible coordinates')
        d=haversine_m(site['latitude'],site['longitude'],row['latitude'],row['longitude'])
        row['distance_m']=round(d,3);row['distance_ring']=ring(d,site['rings_m'])
    for field in ('temperature_C_if_known','capacity_MW_if_known'):
        if row[field] is not None and not asset.get(field+'_source_url'):raise ValueError('Unsupported engineering value: '+field)
    return row

def deduplicate(rows):
    result=[];seen={};keys=set()
    for row in rows:
        key=(row['site_id'],row['asset_id'])
        if key in seen:
            if seen[key]!=row:raise ValueError('Conflicting duplicate asset ID: '+str(key))
            continue
        physical=(row['asset_name'].casefold(),None if row['latitude'] is None else round(row['latitude'],5),None if row['longitude'] is None else round(row['longitude'],5))
        if physical in keys:raise ValueError('Duplicate facility requires review: '+row['asset_name'])
        seen[key]=row;keys.add(physical);result.append(row)
    return result
