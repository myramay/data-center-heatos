"""
Site definitions. One engine, two places: Chelsea (urban, existing data center) and Lansing
(suburban, proposed data center). Everything place-specific is here or in config.SITE_OVERRIDES.
"""
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "data" / "cache"
OUT = HERE / "out"


@dataclass
class Site:
    id: str
    name: str                      # shown on the page
    subtitle: str
    dc_name: str
    dc_latlon: tuple               # (lat, lon) of the heat source
    dc_id: int                     # id used for the data center in the building table / footprints
    cache: Path
    plan_file: str
    montecarlo_file: str
    weather_key: str               # which real-data weather to use
    network_owner: str             # who builds the street pipes in the base case
    scenario_labels: dict
    phase_years: list
    camera: dict
    dc_status: str                 # "commissioned" or "proposed"
    estimated_note: str            # how demand was found when there is no benchmarking data
    context_key: str
    capture_point: str = ""
    capture_options: list = field(default_factory=list)
    architecture: str = ""
    architecture_why: list = field(default_factory=list)
    parties: dict = field(default_factory=dict)

    @property
    def buildings_csv(self): return self.cache / "buildings.csv"
    @property
    def footprints(self): return self.cache / "footprints.geojson"
    @property
    def streets(self): return self.cache / "streets.graphml"
    @property
    def plan_path(self): return OUT / self.plan_file


SITES = {
    "chelsea": Site(
        id="chelsea", name="Chelsea, New York", subtitle="Reusing 111 8th Ave data center heat in Chelsea",
        dc_name="111 8th Avenue", dc_latlon=(40.74136, -74.003208), dc_id=1007390001, cache=CACHE,
        plan_file="plan.json", montecarlo_file="montecarlo.json", weather_key="weather_nyc",
        network_owner="Con Edison", scenario_labels={0.0: "HeatOS pays all street pipes", 0.5: "50% grant on street pipes",
                                                      1.0: "Utility rate-base (Con Ed) pays street pipes"},
        phase_years=[2027, 2030, 2034], camera={"lon": -74.001, "lat": 40.7394, "zoom": 15.2, "bearing": -28, "pitch": 58},
        dc_status="commissioned", estimated_note="no benchmarking data, so its heat use is estimated from floor area",
        context_key="chelsea",
        capture_point="Warm water from the data center's own cooling plant (condenser / chilled-water return, assumed about 27 C, to be confirmed with the operator), taken through a plate heat exchanger in the building's plant room.",
        capture_options=[
            {"name": "Condenser-water loop of the cooling plant", "temp": "typically 28 to 35 C", "verdict": "Chosen",
             "why": "Warm enough for a low-lift heat pump, a simple plate heat exchanger in the plant room, and no change to how the computers are cooled."},
            {"name": "Chilled-water return", "temp": "typically 12 to 20 C", "verdict": "Not chosen",
             "why": "Too cold: the heat pumps would have to lift it much further, using more electricity."},
            {"name": "Liquid cooling taken straight from the racks", "temp": "typically 45 to 60 C", "verdict": "Future upgrade",
             "why": "Best heat quality, but it needs each tenant's equipment changed; a carrier hotel is mostly air-cooled today."}],
        architecture="Ambient two-way loop (about 27 C supply, 17 C return) in plastic pipe under the avenues, with a small heat pump in each customer building.",
        architecture_why=[
            "Dense urban blocks, few places for a central plant: a low-temperature loop needs only a heat exchanger at the data center and a heat pump in each basement.",
            "Water stays close to ground temperature, so the pipes lose very little heat and can be plain plastic. A conventional 80/50 C hot-water network would lose several times more on the same route.",
            "Every customer lifts the heat to exactly the temperature it needs (hot water 60 C, radiators 42 to 65 C), so no heat is over-boosted.",
            "The same loop can later carry heat back from offices in summer or deliver cooling, so the pipe works all year.",
            "Equipment goes indoors above the flood line and is built to the area's noise limit (the block is already at 56 dB)."]),
    "lansing": Site(
        id="lansing", name="Lansing, New York", subtitle="Heat from the proposed Lake Hawkeye data center for Lansing",
        dc_name="Lake Hawkeye data center (proposed)", dc_latlon=(42.6022, -76.6349), dc_id=1,
        cache=CACHE / "lansing", plan_file="plan_lansing.json", montecarlo_file="montecarlo_lansing.json",
        weather_key="weather_ithaca", network_owner="a Town of Lansing / TeraWulf joint venture",
        scenario_labels={0.0: "Venture pays all street pipes", 0.25: "25% grant on street pipes", 0.5: "50% grant on street pipes",
                         1.0: "Grants pay all street pipes"},
        phase_years=[2029, 2032, 2036], camera={"lon": -76.6275, "lat": 42.6012, "zoom": 14.5, "bearing": 25, "pitch": 55},
        dc_status="proposed", estimated_note="no benchmarking data, so its heat use is modeled from county property records (floor area, age, heating fuel)",
        context_key="lansing",
        capture_point="Return water from the liquid-cooling loop of the new data center, about 50 C, tapped at the campus boundary through a plate heat exchanger. The campus rejects its own heat through dry coolers, so the export is a second sink.",
        capture_options=[
            {"name": "Return water of the liquid-cooling loop", "temp": "about 45 to 60 C (assumed 50 C)", "verdict": "Chosen",
             "why": "Hot enough for greenhouses and fish tanks to use directly, so no heat pump and almost no electricity."},
            {"name": "Hot air off the dry coolers", "temp": "about 35 to 45 C, low density", "verdict": "Not chosen",
             "why": "Air carries little heat per cubic metre and the heat exchangers would be large and noisy."},
            {"name": "Chiller condenser water", "temp": "not applicable", "verdict": "Not available",
             "why": "The campus is designed to run mostly dry with no chiller plant to tap."}],
        architecture="Warm direct-use loop (about 50 C supply, 30 C return) in insulated pipe along existing roads, a pit or tank store at the campus edge, and customers on the loop in order of the temperature they need.",
        architecture_why=[
            "The source is already hot (about 50 C) because the computers are liquid-cooled, so greenhouses and fish tanks can use the heat directly with no heat pump.",
            "Customers are spread out, so the network is short and goes first to the large year-round users next to the campus; homes and schools follow only if the heat per metre of pipe justifies it.",
            "A pit or tank store on open land is cheap and rides through short outages; the campus design is closed-loop with dry cooling, so no water is added and nothing is discharged to Cayuga Lake.",
            "Return water at about 30 C is the cascade: the hottest users take heat first, then lower-temperature users, and the coolest water goes back to the data center.",
            "Pipes follow existing roads and avoid wetlands; pumps and controls stay above grade pending the flood review."]),
}


def get_site(site):
    return site if isinstance(site, Site) else SITES[site]
