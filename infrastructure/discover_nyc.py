"""Resolve evidence-reviewed addresses using official location data."""
from .distance import valid_coordinate

def locate(asset,client):
    if asset.get('pluto_address'):
        text=asset['pluto_address'].replace("'","''")
        data,url=client.get_json('https://data.cityofnewyork.us/resource/64uk-42ks.json',{
            '$where':f"borough='MN' AND address='{text}'",'$select':'address,bbl,latitude,longitude,ownername,version','$limit':100})
        matches=[]
        for row in data:
            try:lat,lon=float(row['latitude']),float(row['longitude'])
            except (KeyError,ValueError,TypeError):continue
            if valid_coordinate(lat,lon) and 40.65<lat<40.9 and -74.1<lon<-73.85:matches.append((row,lat,lon))
        if len(matches)==1:
            row,lat,lon=matches[0]
            return dict(latitude=lat,longitude=lon,coordinate_source_url=url,
                        coordinate_method='NYC PLUTO parcel representative point',coordinate_record_id=row['bbl'])
    data,url=client.get_json('https://geocoding.geo.census.gov/geocoder/locations/address',{
        'street':asset['street'],'city':'New York','state':'NY','zip':asset['zip'],
        'benchmark':'Public_AR_Current','format':'json'})
    matches=[]
    for m in data.get('result',{}).get('addressMatches',[]):
        coords=m.get('coordinates',{});lat,lon=coords.get('y'),coords.get('x')
        if (m.get('addressComponents',{}).get('state')=='NY' and
                m.get('addressComponents',{}).get('zip')==asset['zip'] and valid_coordinate(lat,lon)
                and 40.65<lat<40.9 and -74.1<lon<-73.85):matches.append(m)
    if len(matches)!=1:raise ValueError(f'{asset["asset_id"]}: {len(matches)} eligible Census address matches; requires review')
    m=matches[0]
    return dict(latitude=m['coordinates']['y'],longitude=m['coordinates']['x'],coordinate_source_url=url,
                coordinate_method='US Census TIGER address-range interpolation; not equipment position',
                coordinate_record_id=m['tigerLine']['tigerLineId'])
