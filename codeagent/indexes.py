"""Creates collections, regular indexes and Atlas Search / Vector Search indexes."""
from __future__ import annotations

import logging
import time

from . import db as D
from .settings import get_settings

log = logging.getLogger(__name__)


def vector_index_definition(dimensions: int) -> dict:
    return {
        "fields": [
            {"type": "vector", "path": "embedding", "numDimensions": dimensions, "similarity": "cosine"},
            # Pre-filters: permissions first, then optional narrowing.
            {"type": "filter", "path": "access_scope"},
            {"type": "filter", "path": "kind"},
            {"type": "filter", "path": "repo"},
            {"type": "filter", "path": "language"},
        ]
    }


TEXT_INDEX_DEFINITION = {
    "mappings": {
        "dynamic": False,
        "fields": {
            "text": {"type": "string", "analyzer": "lucene.standard"},
            # camelCase / snake_case identifiers split into words at ingest time
            "ident_tokens": {"type": "string", "analyzer": "lucene.standard"},
            "symbol": {"type": "string", "analyzer": "lucene.keyword"},
            "path": {"type": "string", "analyzer": "lucene.standard"},
            "access_scope": {"type": "token"},
            "kind": {"type": "token"},
            "repo": {"type": "token"},
            "language": {"type": "token"},
        },
    }
}


def _ensure_collection(database, name: str, **options) -> None:
    if name not in database.list_collection_names():
        try:
            database.create_collection(name, **options)
        except Exception as exc:  # created concurrently by another service
            if "already exists" not in str(exc):
                raise


def ensure_collections(database=None) -> None:
    from pymongo import ASCENDING, DESCENDING

    settings = get_settings()
    database = database if database is not None else D.get_db()

    _ensure_collection(database, D.CHUNKS)
    _ensure_collection(database, D.FILES)
    _ensure_collection(
        database,
        D.EVENTS,
        timeseries={"timeField": "ts", "metaField": "meta", "granularity": "minutes"},
        expireAfterSeconds=settings.events_ttl_days * 86400,
    )
    _ensure_collection(database, D.OUTCOMES)
    _ensure_collection(database, D.ROLLUPS)
    _ensure_collection(database, D.INGEST_RUNS)
    _ensure_collection(database, D.EVAL_RUNS)
    _ensure_collection(database, D.AUDIT)

    database[D.CHUNKS].create_index([("repo", ASCENDING), ("path", ASCENDING)])
    database[D.FILES].create_index([("repo", ASCENDING), ("path", ASCENDING)], unique=True)
    database[D.EVENTS].create_index([("meta.user", ASCENDING), ("ts", DESCENDING)])
    database[D.OUTCOMES].create_index([("ts", ASCENDING)], expireAfterSeconds=settings.events_ttl_days * 86400)
    database[D.OUTCOMES].create_index([("user", ASCENDING), ("ts", DESCENDING)])
    database[D.ROLLUPS].create_index([("hour", DESCENDING)])
    database[D.INGEST_RUNS].create_index([("started_at", DESCENDING)])
    database[D.EVAL_RUNS].create_index([("started_at", DESCENDING)])
    database[D.AUDIT].create_index([("ts", DESCENDING)])


def ensure_search_indexes(database=None, wait: bool = True, timeout: int = 600) -> None:
    from pymongo.operations import SearchIndexModel

    settings = get_settings()
    database = database if database is not None else D.get_db()
    coll = database[D.CHUNKS]
    wanted = {
        D.VECTOR_INDEX: ("vectorSearch", vector_index_definition(settings.embed_dimensions)),
        D.TEXT_INDEX: ("search", TEXT_INDEX_DEFINITION),
    }
    existing = {ix["name"]: ix for ix in coll.list_search_indexes()}
    for name, (kind, definition) in wanted.items():
        if name not in existing:
            log.info("Creating %s index %s", kind, name)
            coll.create_search_index(SearchIndexModel(definition=definition, name=name, type=kind))
        else:
            current = existing[name].get("latestDefinition") or {}
            if _normalise(current) != _normalise(definition):
                log.info("Updating %s index %s", kind, name)
                coll.update_search_index(name, definition)
    if wait:
        wait_for_search_indexes(coll, list(wanted), timeout)


def _normalise(definition: dict) -> dict:
    """Atlas returns definitions with defaults filled in; compare only the parts we set."""
    if "fields" in definition and isinstance(definition["fields"], list):
        return {"fields": sorted(
            ({k: f.get(k) for k in ("type", "path", "numDimensions", "similarity")} for f in definition["fields"]),
            key=lambda f: (f["path"], f["type"]),
        )}
    mappings = definition.get("mappings", {})
    return {"fields": sorted(mappings.get("fields", {}).keys())}


def wait_for_search_indexes(coll, names: list[str], timeout: int) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = {ix["name"]: ix for ix in coll.list_search_indexes()}
        pending = [n for n in names if not status.get(n, {}).get("queryable")]
        if not pending:
            log.info("Search indexes ready: %s", ", ".join(names))
            return
        log.info("Waiting for search indexes to become queryable: %s", ", ".join(pending))
        time.sleep(10)
    log.warning("Search indexes not queryable after %ss; continuing anyway", timeout)
