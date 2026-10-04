"""Independent tests; no live network calls and no simulator imports."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from infrastructure.config import read_site
from infrastructure.distance import haversine_m,ring
from infrastructure.discover_nyc import locate
from infrastructure.normalize import normalize,deduplicate
from infrastructure.export import export

class InfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.site=read_site()
        self.asset=dict(asset_id='test',asset_name='Test facility',role='thermal_plant',confidence='MEDIUM',
                        source_url='https://example.org',evidence_note='Synthetic test only',location_scope='facility_address')

    def test_reuses_demand_coordinates_and_rings(self):
        self.assertEqual(self.site['latitude'],40.740704)
        self.assertEqual(self.site['longitude'],-74.001844)
        self.assertEqual(self.site['rings_m'],[250,500,1000,1500,2200])
        self.assertEqual(self.site['site_id'],'chelsea')

    def test_distance_and_ring_boundaries(self):
        self.assertEqual(haversine_m(0,0,0,0),0)
        self.assertAlmostEqual(haversine_m(0,0,0,1),6371000*math.pi/180,places=6)
        self.assertEqual(ring(250,self.site['rings_m']),'<= 250 m')
        self.assertEqual(ring(250.01,self.site['rings_m']),'<= 500 m')
        self.assertIsNone(ring(2200.01,self.site['rings_m']))
        with self.assertRaises(ValueError):haversine_m(91,0,0,0)

    def test_unknown_numeric_values_stay_null(self):
        r=normalize(self.asset,dict(latitude=40.74,longitude=-74),self.site)
        self.assertIsNone(r['temperature_C_if_known']);self.assertIsNone(r['capacity_MW_if_known'])
        with self.assertRaises(ValueError):normalize(self.asset|{'capacity_MW_if_known':12},dict(latitude=40.74,longitude=-74),self.site)

    def test_service_area_has_no_fake_point(self):
        a=self.asset|{'location_scope':'service_territory_not_point','role':'existing_heat_network'}
        r=normalize(a,{},self.site)
        self.assertIsNone(r['distance_m']);self.assertEqual(r['distance_ring'],'unknown')
        with tempfile.TemporaryDirectory() as d:
            export([r],Path(d))
            j=json.loads((Path(d)/'infrastructure_candidates.geojson').read_text())
            self.assertIsNone(j['features'][0]['geometry'])

    def test_deduplication_and_conflicts(self):
        r=normalize(self.asset,dict(latitude=40.74,longitude=-74),self.site)
        self.assertEqual(len(deduplicate([r,r])),1)
        with self.assertRaises(ValueError):deduplicate([r,r|{'asset_name':'Different'}])
        with self.assertRaises(ValueError):deduplicate([r,r|{'asset_id':'different-id'}])

    def test_wrong_state_geocode_rejected(self):
        class Fake:
            def get_json(self,*args,**kwargs):
                return {'result':{'addressMatches':[{'addressComponents':{'state':'KS','zip':'10011'},'coordinates':{'y':39.18,'x':-96.58}}]}},'https://example.org'
        with self.assertRaises(ValueError):locate({'asset_id':'test','street':'401 W 16th St','zip':'10011'},Fake())

    def test_evidence_required(self):
        with self.assertRaises(ValueError):normalize(self.asset|{'source_url':''},dict(latitude=40.74,longitude=-74),self.site)

if __name__=='__main__':unittest.main()
