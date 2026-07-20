from app.modules.routing import seed


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


def test_seed_shelters_inserts_seed_file_rows_when_empty():
    db = _SeedDb(existing_count=0)
    seed.seed_shelters(db)
    assert db.committed
    assert len(db.added) == 4  # one per shelters_seed.json entry
    assert all(row.status == "open" for row in db.added)
    assert all(row.geom.startswith("SRID=4326;POINT(") for row in db.added)


def test_seed_shelters_skips_when_shelters_already_exist():
    db = _SeedDb(existing_count=1)
    seed.seed_shelters(db)
    assert db.added == []
    assert not db.committed
