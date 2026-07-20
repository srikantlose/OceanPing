from app.modules.inundation import seed


class _FakeQuery:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class _SeedDb:
    def __init__(self, existing_count=0):
        self._existing_count = existing_count
        self.added = []
        self.committed = False

    def query(self, model):
        return _FakeQuery(self._existing_count)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True


def test_seed_elevation_cells_inserts_seed_file_rows_when_empty():
    db = _SeedDb(existing_count=0)
    seed.seed_elevation_cells(db)
    assert db.committed
    assert len(db.added) > 0  # real Copernicus DEM extract, not a fixed illustrative count
    assert all(isinstance(row.elevation_m, float) for row in db.added)
    assert all(row.h3_cell for row in db.added)


def test_seed_elevation_cells_skips_when_cells_already_exist():
    db = _SeedDb(existing_count=1)
    seed.seed_elevation_cells(db)
    assert db.added == []
    assert not db.committed
