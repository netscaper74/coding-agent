import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["CONFIG_DIR"] = str(ROOT / "config")
os.environ.setdefault("OPENAI_API_KEY", "test-openai")
os.environ.setdefault("VOYAGE_API_KEY", "test-voyage")


class FakeCollection:
    def __init__(self, name, db):
        self.name, self.db = name, db
        self.docs = []

    def aggregate(self, pipeline):
        return self.db.aggregate_handler(self.name, pipeline)

    def find_one(self, flt=None, *args, **kwargs):
        for d in self.docs:
            if all(_match(d, k, v) for k, v in (flt or {}).items()):
                return d
        return None

    def insert_one(self, doc):
        self.docs.append(doc)

    def insert_many(self, docs, **kwargs):
        self.docs.extend(docs)

    def find(self, flt=None, projection=None):
        return [d for d in self.docs if all(_match(d, k, v) for k, v in (flt or {}).items())]

    def delete_many(self, flt):
        self.docs = [d for d in self.docs if not all(_match(d, k, v) for k, v in flt.items())]

    def update_one(self, flt, update, upsert=False):
        doc = self.find_one(flt)
        if doc is None and upsert:
            doc = dict(flt)
            self.docs.append(doc)
        if doc is not None:
            doc.update(update.get("$set", {}))

    def update_many(self, flt, update):
        for d in self.find(flt):
            d.update(update.get("$set", {}))


def _match(doc, key, cond):
    value = doc.get(key)
    if isinstance(cond, dict) and "$in" in cond:
        values = value if isinstance(value, list) else [value]
        return any(v in cond["$in"] for v in values)
    if isinstance(cond, dict) and "$ne" in cond:
        return value != cond["$ne"]
    return value == cond


class FakeDB:
    def __init__(self):
        self.collections = {}
        self.pipelines = []
        self.aggregate_handler = lambda name, pipeline: []

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection(name, self))


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture(autouse=True)
def _fresh_settings():
    from codeagent import settings, users
    from codeagent.api import events

    settings.get_settings.cache_clear()
    users._directory.cache_clear()
    events.pricing.cache_clear()
    yield
