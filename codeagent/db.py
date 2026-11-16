"""MongoDB Atlas connection and collection names."""
from __future__ import annotations

from functools import lru_cache

from .settings import get_settings

CHUNKS = "chunks"            # code + standards chunks with embeddings
FILES = "files"              # per-file fingerprints for incremental ingest
INGEST_RUNS = "ingest_runs"  # one document per ingest run
EVENTS = "events"            # time-series: one document per agent request
OUTCOMES = "outcomes"        # accept/reject/feedback events from Continue
ROLLUPS = "events_rollup"    # hourly aggregates for the dashboard
EVAL_RUNS = "eval_runs"      # eval job results
AUDIT = "audit"              # admin actions (impersonation in playground, etc.)

VECTOR_INDEX = "chunks_vector"
TEXT_INDEX = "chunks_text"


@lru_cache(maxsize=1)
def get_client():
    from pymongo import MongoClient

    settings = get_settings()
    if not settings.mongodb_uri:
        raise RuntimeError("MONGODB_URI is not set. Put your Atlas connection string in .env")
    return MongoClient(settings.mongodb_uri, appname="coding-agent-demo", tz_aware=True)


def get_db():
    return get_client()[get_settings().mongodb_db]
