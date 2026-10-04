"""
Pipe network: ONE tree of pipes along real streets, starting at the data center.

Idea (easy to explain): compute the shortest street route from the data center to every
intersection. Those routes overlap, so together they form a tree. A pipe segment is paid for
once, no matter how many buildings hang off it. Buildings connect to the nearest intersection
with a short lateral pipe.

(A shortest-path tree is not the mathematically cheapest possible network (a Steiner tree),
but it is close for a street grid, easy to see on a map, and fast to solve.)
"""
import networkx as nx
import numpy as np
import osmnx as ox

from data_load import _to_m


class PipeTree:
    def __init__(self, bx, by, site):
        """bx, by: building positions in metres (UTM), arrays. site: sites.Site (street graph + data center)."""
        g = ox.load_graphml(site.streets)

        # Collapse the OSM multigraph into a simple undirected graph (shortest edge per pair).
        G = nx.Graph()
        self.coords = {}            # (u, v) -> [[lon, lat], ...] oriented u -> v
        for u, v, d in g.edges(data=True):
            L = float(d["length"])
            if u == v or (G.has_edge(u, v) and G[u][v]["length"] <= L):
                continue
            G.add_edge(u, v, length=L)
            if "geometry" in d:
                pts = [list(p) for p in d["geometry"].coords]
            else:
                pts = [[g.nodes[u]["x"], g.nodes[u]["y"]], [g.nodes[v]["x"], g.nodes[v]["y"]]]
            # make sure the line really starts at u
            if np.hypot(pts[0][0] - g.nodes[u]["x"], pts[0][1] - g.nodes[u]["y"]) > \
               np.hypot(pts[-1][0] - g.nodes[u]["x"], pts[-1][1] - g.nodes[u]["y"]):
                pts = pts[::-1]
            self.coords[(u, v)] = pts
            self.coords[(v, u)] = pts[::-1]

        self.nodes = list(G.nodes)
        lon = np.array([g.nodes[n]["x"] for n in self.nodes])
        lat = np.array([g.nodes[n]["y"] for n in self.nodes])
        self.node_lonlat = {n: (lo, la) for n, lo, la in zip(self.nodes, lon, lat)}
        nx_, ny_ = _to_m.transform(lon, lat)

        # Root = street intersection closest to the data center.
        dcx, dcy = _to_m.transform(site.dc_latlon[1], site.dc_latlon[0])
        self.root = self.nodes[int(np.argmin(np.hypot(nx_ - dcx, ny_ - dcy)))]
        self.dc_to_root_m = float(np.min(np.hypot(nx_ - dcx, ny_ - dcy)))

        # Shortest street path from root to every node = the tree.
        self.dist, paths = nx.single_source_dijkstra(G, self.root, weight="length")
        self.pred = {n: p[-2] for n, p in paths.items() if len(p) > 1}
        self.edge_len = {n: G[self.pred[n]][n]["length"] for n in self.pred}   # edge id = child node
        self._paths = paths

        # Each building attaches to its nearest reachable intersection.
        reach = [i for i, n in enumerate(self.nodes) if n in paths]
        rx, ry = nx_[reach], ny_[reach]
        self.attach, self.lateral_m = [], []
        for x, y in zip(bx, by):
            j = int(np.argmin(np.hypot(rx - x, ry - y)))
            self.attach.append(self.nodes[reach[j]])
            self.lateral_m.append(float(np.hypot(rx[j] - x, ry[j] - y)))

    def path_edges(self, node):
        """Edge ids (child nodes) from the root down to `node`."""
        p = self._paths[node]
        return p[1:]

    def edge_coords(self, child):
        return self.coords[(self.pred[child], child)]
