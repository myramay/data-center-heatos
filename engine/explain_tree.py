"""The "Why?" panel: a readable decision tree that explains the recommendations.

5,000 synthetic building variants per site are labelled with recommend.py's
choice (best option, or not_connected when no option has positive value NPV),
then a shallow DecisionTreeClassifier (max_depth 5) learns those labels from
human-readable features. Any building's path through the tree becomes a list
of yes/no questions with sample counts and a confidence per node.

ML_TEAM_INTEGRATION: node confidence is leaf/node purity for now; the Monte
Carlo engine will replace it with P(choice stays best) across futures.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from sklearn.tree import DecisionTreeClassifier

from engine import providers
from engine.config import load_site
from engine.contracts import Building, SiteId
from engine.recommend import evaluate_building, plan_details

N_VARIANTS = 5000
MAX_DEPTH = 5
INTENSITY_KWH_M2 = {"residential": 140, "public_housing": 160, "office": 95, "retail": 85, "food": 260,
                    "clinic": 210, "school": 120, "greenhouse": 350, "aquaculture": 450, "home": 150}

NUMERIC = [
    ("on_steam", "On Con Ed steam"),
    ("street_distance_m", "Pipe distance (m)"),
    ("annual_heat_mwh", "Annual heat (MWh)"),
    ("required_supply_temp_c", "Required supply temperature (C)"),
    ("current_heat_cost_usd_per_mwh", "Current heat cost ($/MWh)"),
    ("equity_score", "Equity score"),
    ("boiler_age_years", "Boiler age (years)"),
]
LABELS = {
    "direct_link": "Direct link to the data center", "loop_hp": "Loop + building heat pump",
    "steam_hp": "Steam heat pump", "direct_use": "Direct use of warm loop", "booster": "Warm loop + hot-water booster",
    "not_connected": "Not connected",
}


def _features(b: Building, use_types: list[str]) -> list[float]:
    return [float(b.heating_system == "steam"), b.street_distance_m, b.annual_heat_mwh, b.required_supply_temp_c,
            b.current_heat_cost_usd_per_mwh, b.equity_score,
            -1.0 if b.boiler_age_years is None else float(b.boiler_age_years)] + \
           [float(b.use_type == u) for u in use_types]


def _variants(site: SiteId, n: int, seed: int) -> list[Building]:
    """Synthetic buildings resembling the site's inventory, for labelling."""
    cfg = load_site(site)
    real = providers.get_buildings(site)
    rng = np.random.default_rng(seed)
    uses = [b.use_type for b in real]
    systems = [b.heating_system for b in real]
    max_d = max(b.street_distance_m for b in real) * 1.2
    out = []
    for i in range(n):
        tmpl = real[rng.integers(len(real))]
        use = uses[rng.integers(len(uses))] if rng.random() < 0.5 else tmpl.use_type
        system = systems[rng.integers(len(systems))]
        heat = float(np.exp(rng.normal(np.log(max(tmpl.annual_heat_mwh, 50)), 0.6)))
        fuel = cfg.prices.fuels_usd_per_mwh.get(system) or cfg.prices.fuels_usd_per_mwh["gas_boiler"]
        eff = (cfg.backup_efficiency.get(system) or cfg.backup_efficiency["unknown"]).value
        cost = (fuel.value / eff + 8.0) * rng.uniform(0.85, 1.15)
        if site == "lansing":
            req = {"greenhouse": 40.0, "aquaculture": 28.0}.get(use, 45.0)
        else:
            req = 110.0 if system == "steam" else float(rng.choice([50.0, 60.0, 70.0, 75.0]))
        floor = heat * 1000 / INTENSITY_KWH_M2.get(use, 140)
        out.append(Building(
            id=f"V{i}", name="variant", site=site, lat=0, lon=0, x_m=0, y_m=0,
            street_distance_m=float(rng.uniform(20, max_d)), height_m=10, footprint_m2=max(floor / 4, 1),
            floor_area_m2=max(floor, 1), use_type=use, year_built=1960, heating_system=system,
            annual_heat_mwh=heat, current_heat_cost_usd_per_mwh=float(cost), required_supply_temp_c=req,
            boiler_age_years=None if system == "steam" else float(rng.uniform(0, 40)),
            equity_score=float(rng.uniform(0, 1)), is_estimated=True))
    return out


class SiteTree:
    def __init__(self, site: SiteId, n: int = N_VARIANTS, seed: int = 11):
        self.site = site
        cfg = load_site(site)
        real = providers.get_buildings(site)
        self.use_types = sorted({b.use_type for b in real})
        self.feature_names = [k for k, _ in NUMERIC] + [f"use_{u}" for u in self.use_types]
        self.feature_labels = [lab for _, lab in NUMERIC] + [f"Use type is {u.replace('_', ' ')}" for u in self.use_types]
        variants = _variants(site, n, seed)
        X = np.array([_features(b, self.use_types) for b in variants])
        y = []
        for b in variants:
            ev = evaluate_building(cfg, b)
            y.append(ev.best.option if ev.best else "not_connected")
        self.clf = DecisionTreeClassifier(max_depth=MAX_DEPTH, min_samples_leaf=40, random_state=0).fit(X, np.array(y))
        self.train_accuracy = float(self.clf.score(X, y))
        self.n_samples = n

    # -------------------------------------------------------------- helpers

    def _question(self, node: int) -> tuple[str, str, float]:
        t = self.clf.tree_
        f, thr = int(t.feature[node]), float(t.threshold[node])
        name = self.feature_names[f]
        if name == "on_steam" or name.startswith("use_"):
            return f"{self.feature_labels[f]}?", name, thr
        if name == "boiler_age_years" and thr < 1:
            return "Steam-heated (no boiler of its own)?", name, thr     # steam encoded as boiler age -1
        return f"{self.feature_labels[f]} <= {thr:,.1f}?", name, thr

    def _node(self, node: int) -> dict:
        t = self.clf.tree_
        counts = t.value[node][0] * t.n_node_samples[node] if t.value[node][0].sum() <= 1.0 + 1e-9 else t.value[node][0]
        dist = {str(c): int(round(v)) for c, v in zip(self.clf.classes_, counts)}
        total = max(sum(dist.values()), 1)
        top = max(dist, key=dist.get)
        return {"id": node, "samples": int(t.n_node_samples[node]), "distribution": dist,
                "majority": top, "majority_label": LABELS.get(top, top), "purity": round(dist[top] / total, 4)}

    # -------------------------------------------------------------- public

    def to_json(self) -> dict:
        t = self.clf.tree_

        def walk(node: int) -> dict:
            d = self._node(node)
            if t.children_left[node] == -1:
                d["leaf"] = True
                return d
            q, feat, thr = self._question(node)
            binary = feat == "on_steam" or feat.startswith("use_")
            # sklearn sends <= threshold left; for 0/1 features "<= 0.5" means "no"
            d.update(leaf=False, question=q, feature=feat, threshold=thr,
                     yes=walk(int(t.children_right[node] if binary else t.children_left[node])),
                     no=walk(int(t.children_left[node] if binary else t.children_right[node])))
            return d

        return {"site": self.site, "max_depth": MAX_DEPTH, "n_samples": self.n_samples,
                "train_accuracy": round(self.train_accuracy, 4), "classes": list(map(str, self.clf.classes_)),
                "labels": LABELS, "root": walk(0),
                "confidence_source": "node purity (ML_TEAM_INTEGRATION: Monte Carlo probability)"}

    def explain(self, b: Building) -> dict:
        x = np.array([_features(b, self.use_types)])
        t = self.clf.tree_
        path = self.clf.decision_path(x).indices
        nodes = []
        for node, nxt in zip(path, list(path[1:]) + [None]):
            d = self._node(int(node))
            if nxt is None:
                d.update(leaf=True, question=None, answer=None)
            else:
                q, feat, thr = self._question(int(node))
                went_left = int(nxt) == int(t.children_left[node])
                binary = feat == "on_steam" or feat.startswith("use_")
                answer = (not went_left) if binary else went_left
                value = x[0][self.feature_names.index(feat)]
                d.update(leaf=False, question=q, feature=feat, answer="yes" if answer else "no",
                         value=None if binary else round(float(value), 2))
            d["confidence"] = d["purity"]
            nodes.append(d)
        predicted = str(self.clf.predict(x)[0])
        return {"building_id": b.id, "path": nodes, "tree_choice": predicted,
                "tree_choice_label": LABELS.get(predicted, predicted)}


@lru_cache(maxsize=4)
def site_tree(site: SiteId) -> SiteTree:
    return SiteTree(site)


def explain_building(site: SiteId, building_id: str) -> dict:
    b = next((x for x in providers.get_buildings(site) if x.id == building_id), None)
    if b is None:
        raise KeyError(building_id)
    details = plan_details(site)
    item = next(i for i in details.plan.items if i.building_id == building_id)
    out = site_tree(site).explain(b)
    ev = details.evals[building_id]
    out.update(
        recommendation=item.model_dump(), recommendation_label=LABELS.get(item.option, item.option),
        options=ev.to_dict()["options"],
        agrees=out["tree_choice"] == item.option or (out["tree_choice"] != "not_connected" and item.connect is False
                                                      and "capacity_limit" in item.reason_codes),
        note=("Economically worthwhile, but the data center's firm capacity is already allocated."
              if "capacity_limit" in item.reason_codes else None))
    return out
