import numpy as np

from app.modules.geo.h3utils import cell_for
from app.modules.nlp.dedup import cosine, should_merge, spatially_adjacent

CELL_A = cell_for(13.0827, 80.2707)
CELL_NEIGHBOR = cell_for(13.0900, 80.2707)   # a few hundred metres north
CELL_FAR = cell_for(13.5000, 80.2707)        # ~46 km north


def test_spatial_adjacency():
    assert spatially_adjacent(CELL_A, [CELL_A])
    assert spatially_adjacent(CELL_A, [CELL_NEIGHBOR])
    assert not spatially_adjacent(CELL_A, [CELL_FAR])


def test_merge_requires_spatial_proximity():
    assert not should_merge(CELL_A, [CELL_FAR])


def test_merge_without_embeddings_is_spatial_only():
    assert should_merge(CELL_A, [CELL_NEIGHBOR])


def test_merge_with_embeddings_requires_similarity():
    same = np.array([1.0, 0.0, 0.0])
    similar = np.array([0.9, 0.1, 0.0])
    different = np.array([0.0, 1.0, 0.0])
    assert should_merge(CELL_A, [CELL_A], same, similar)
    assert not should_merge(CELL_A, [CELL_A], same, different)


def test_cosine_edge_cases():
    assert cosine([0, 0], [1, 1]) == 0.0
    assert abs(cosine([1, 0], [1, 0]) - 1.0) < 1e-9
