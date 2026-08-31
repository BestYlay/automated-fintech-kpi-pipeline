from __future__ import annotations

import argparse
from datetime import date

from .pipeline import bootstrap, daily, export_day, init_db, run_day, validate_day
from .publisher import publish_marts


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated FinTech KPI pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    bootstrap_parser = commands.add_parser("bootstrap")
    bootstrap_parser.add_argument("--start", type=_date, required=True)
    bootstrap_parser.add_argument("--end", type=_date, required=True)
    bootstrap_parser.add_argument("--workers", type=int, default=2)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--date", type=_date, required=True)
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("--date", type=_date, required=True)
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--date", type=_date, required=True)
    daily_parser = commands.add_parser("daily")
    daily_parser.add_argument("--through", type=_date, required=True)
    daily_parser.add_argument("--workers", type=int, default=2)
    daily_parser.add_argument("--lookback-days", type=int, default=45)
    daily_parser.add_argument("--publish", action="store_true")
    publish_parser = commands.add_parser("publish-marts")
    publish_parser.add_argument("--start", type=_date, required=True)
    publish_parser.add_argument("--end", type=_date, required=True)
    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
        print("database initialized")
    elif args.command == "bootstrap":
        bootstrap(args.start, args.end, workers=args.workers)
        print("bootstrap complete")
    elif args.command == "run":
        result = run_day(args.date)
        print(result)
        if result["status"] != "success":
            raise SystemExit(2)
    elif args.command == "validate":
        for check in validate_day(args.date):
            print(check)
    elif args.command == "export":
        print(export_day(args.date))
    elif args.command == "daily":
        result = daily(
            args.through,
            workers=args.workers,
            lookback_days=args.lookback_days,
            publish=args.publish,
        )
        print(result)
    elif args.command == "publish-marts":
        print(publish_marts(args.start, args.end))


if __name__ == "__main__":
    main()
