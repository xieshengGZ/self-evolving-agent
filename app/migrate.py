"""One-time migration: state/session.json -> SQLite (state/agent.db)."""
from __future__ import annotations

import argparse
import json

from app.env import load_dotenv

load_dotenv()

from app import config  # noqa: E402
from app.storage import Storage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate session.json to SQLite")
    parser.add_argument("--json", default=config.SESSION_PATH, help="source session.json")
    parser.add_argument("--db", default=config.DB_PATH, help="target agent.db")
    args = parser.parse_args()

    config.ensure_dirs()
    storage = Storage(args.db)
    result = storage.migrate_json(args.json)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(storage.totals(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
