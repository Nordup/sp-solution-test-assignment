"""Persistent aggregate allowance. Reusing a session never resets its ledger."""

import argparse
import json

from browser_agent.config import Settings
from browser_agent.runner import safe_name
from browser_agent.storage import Store


def release_store(settings):
    settings.prepare()
    return Store(settings.artifact_dir / "state" / "operations.sqlite")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "status"])
    parser.add_argument("--session", required=True)
    parser.add_argument("--max-total-usd", type=float)
    args = parser.parse_args(argv)
    store = release_store(Settings.load())
    name = "release:" + safe_name(args.session)
    if args.command == "init":
        if args.max_total_usd is None or not 0 < args.max_total_usd <= 1000:
            parser.error("init requires --max-total-usd in (0,1000]")
        result = store.create_budget(
            name, round(args.max_total_usd * 1_000_000), "release"
        )
    else:
        result = store.budget(name)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
