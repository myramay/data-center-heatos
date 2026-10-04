import math

def valid_coordinate(lat,lon):
    return (isinstance(lat,(float,int)) and isinstance(lon,(float,int)) and
            math.isfinite(lat) and math.isfinite(lon) and -90<=lat<=90 and -180<=lon<=180)

def haversine_m(lat1,lon1,lat2,lon2):
    if not valid_coordinate(lat1,lon1) or not valid_coordinate(lat2,lon2):
        raise ValueError('Invalid WGS84 coordinate')
    p1,p2=math.radians(lat1),math.radians(lat2)
    a=math.sin((p2-p1)/2)**2+math.cos(p1)*math.cos(p2)*math.sin(math.radians(lon2-lon1)/2)**2
    # Match the demand script's Earth-radius convention.
    return 6371000.0*2*math.asin(min(1.0,math.sqrt(a)))

def ring(distance,rings):
    return next((f'<= {r} m' for r in sorted(rings) if distance<=r),None)
