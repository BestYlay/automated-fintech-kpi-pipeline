from __future__ import annotations

import argparse
from datetime import date

from .pipeline import bootstrap, export_day, init_db, run_day, validate_day


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated FinTech KPI pipeline")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    bootstrap_parser = commands.add_parser("bootstrap")
    bootstrap_parser.add_argument("--start", type=_date, required=True)
    bootstrap_parser.add_argument("--end", type=_date, required=True)
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--date", type=_date, required=True)
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("--date", type=_date, required=True)
    export_parser = commands.add_parser("export")
    export_parser.add_argument("--date", type=_date, required=True)
    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
        print("database initialized")
    elif args.command == "bootstrap":
        bootstrap(args.start, args.end)
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


if __name__ == "__main__":
    main()

