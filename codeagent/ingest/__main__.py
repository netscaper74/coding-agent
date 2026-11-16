"""`python -m codeagent.ingest [--once]` — create indexes, ingest, then re-run on an interval."""
from __future__ import annotations

import argparse
import logging
import time

from .. import db as D
from ..indexes import ensure_collections, ensure_search_indexes
from ..settings import get_settings
from .pipeline import run_once


def main() -> None:
    parser = argparse.ArgumentParser(description="Index repositories and coding standards into MongoDB Atlas")
    parser.add_argument("--once", action="store_true", help="run a single ingest and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s ingest %(levelname)s %(message)s")
    log = logging.getLogger("ingest")

    settings = get_settings()
    database = D.get_db()
    ensure_collections(database)
    ensure_search_indexes(database, wait=False)

    while True:
        try:
            run = run_once(database)
            log.info("Ingest finished: ok=%s embed_tokens=%d", run["ok"], run["embed_tokens"])
        except Exception:
            log.exception("Ingest run failed")
        ensure_search_indexes(database, wait=True, timeout=600)
        if args.once or settings.ingest_interval_seconds <= 0:
            break
        log.info("Next ingest in %ss", settings.ingest_interval_seconds)
        time.sleep(settings.ingest_interval_seconds)


if __name__ == "__main__":
    main()
