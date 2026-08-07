"""Generate reproducible CVRP instances (clustered Euclidean, TSPLIB format).

Usage:
    python verification/generate_instances.py

Writes TSPLIB-style .vrp files under data/instances/.
Instances are deterministic (fixed seed) so results are reproducible everywhere.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

SEED = 42
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "instances"

# (name, num_customers, num_clusters, num_vehicles_approx, seed_key)
# `seed_key` fixes the RNG stream per instance. The keys are the original
# G-* identifiers: the dataset was first released under those names, and
# keeping the keys means the coordinates/demands stay byte-identical to the
# validated release. Do not change them.
SPECS = [
    ("VRP-19-2", 19, 2, 2, "G-n19-k2"),
    ("VRP-21-3", 21, 3, 3, "G-n21-k3"),
    ("VRP-22-4", 22, 3, 4, "G-n22-k4"),
    ("VRP-32-5", 32, 4, 5, "G-n32-k5"),
    ("VRP-37-6", 37, 4, 6, "G-n37-k6"),
    ("VRP-45-6", 45, 5, 6, "G-n45-k6"),
    ("VRP-45-7", 45, 5, 7, "G-n45-k7"),
    ("VRP-48-7", 48, 5, 7, "G-n48-k7"),
    ("VRP-54-8", 54, 6, 8, "G-n54-k8"),
    ("VRP-55-8", 55, 6, 8, "G-n55-k8"),
    ("VRP-60-9", 60, 6, 9, "G-n60-k9"),
    ("VRP-60-10", 60, 6, 10, "G-n60-k10"),
]


def _round_dist(x1: float, y1: float, x2: float, y2: float) -> int:
    return int(round(math.hypot(x1 - x2, y1 - y2)))


def generate_instance(
    name: str, num_customers: int, num_clusters: int, vehicles: int, seed_key: str
) -> str:
    rng = random.Random(f"{SEED}:{seed_key}")
    # Cluster centers spread over a 100x100 region.
    centers = [
        (rng.uniform(15, 85), rng.uniform(15, 85)) for _ in range(num_clusters)
    ]
    # Depot at a central-ish position.
    depot = (50.0, 50.0)

    coords = [depot]
    for i in range(num_customers):
        cx, cy = centers[i % len(centers)]
        x = min(100, max(1, round(cx + rng.gauss(0, 7.0))))
        y = min(100, max(1, round(cy + rng.gauss(0, 7.0))))
        coords.append((x, y))

    demand = [0] + [rng.randint(5, 35) for _ in range(num_customers)]
    total_demand = sum(demand)
    # Capacity tuned so the instance is feasible with `vehicles` vehicles.
    capacity = max(30, math.ceil(total_demand / vehicles * 1.25 / 5) * 5)

    dim = num_customers + 1
    lines = [
        f"NAME: {name}",
        "COMMENT: generated clustered CVRP instance (deterministic)",
        "TYPE: CVRP",
        f"DIMENSION: {dim}",
        f"CAPACITY: {capacity}",
        "NODE_COORD_SECTION",
    ]
    for idx, (x, y) in enumerate(coords, start=1):
        lines.append(f"{idx} {x} {y}")
    lines.append("DEMAND_SECTION")
    for idx in range(1, dim + 1):
        lines.append(f"{idx} {demand[idx - 1]}")
    lines.append("DEPOT_SECTION")
    lines.append("1")
    lines.append("EOF")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, n, k, v, seed_key in SPECS:
        text = generate_instance(name, n, k, v, seed_key)
        (OUT_DIR / f"{name}.vrp").write_text(
            text, encoding="ascii", newline="\n"  # force LF: byte-identical on any OS
        )
        print(
            f"wrote {name}.vrp  (customers={n}, "
            f"capacity={text.split('CAPACITY: ')[1].split()[0]})"
        )


if __name__ == "__main__":
    main()
