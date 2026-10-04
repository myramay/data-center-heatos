"""Optional mapped leads. Never automatically promote OSM hits to engineering evidence."""
def discover(site,client):
    area=f'(around:{max(site["rings_m"])},{site["latitude"]},{site["longitude"]})'
    query='[out:json][timeout:25];('+''.join('nwr'+area+tag+';' for tag in [
        '["power"="plant"]','["man_made"="wastewater_plant"]','["man_made"="pumping_station"]',
        '["man_made"="pipeline"]["substance"~"steam|hot_water"]'])+');out center tags;'
    data,url=client.get_json('https://overpass-api.de/api/interpreter',{'data':query})
    return dict(source_url=url,review_status='unreviewed_not_exported_as_candidates',elements=data.get('elements',[]))
