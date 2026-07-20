"""Bathtub inundation model — pure functions, no I/O.

A cell floods once its ground elevation is at or below the given water
level; depth is simply the difference. This is deliberately the simplest
hydrologically-defensible model — no flow routing, no time dimension, and no
connectivity check (a low-lying cell cut off from the sea by a ridge still
"floods" here, same as an actual bathtub). The phase-3 plan names ANUGA
hydrodynamic simulation as the upgrade path; out of scope for this milestone.
"""


def flooded_cells(elevations: dict[str, float], water_level_m: float) -> dict[str, float]:
    """cell_id -> depth_m for every cell at or below water_level_m.

    Monotonic in water_level_m by construction — raising the level can only
    add cells or deepen existing ones, never remove one.
    """
    return {
        cell: round(water_level_m - elevation, 3)
        for cell, elevation in elevations.items()
        if elevation <= water_level_m
    }
