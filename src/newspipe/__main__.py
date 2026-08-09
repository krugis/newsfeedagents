"""CLI entry point: ``python -m newspipe <command>``."""

from __future__ import annotations

import argparse

from newspipe.db.migrate import migrate


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="newspipe", description="GenAI/ML news ingestion pipeline"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("migrate", help="Apply pending DB migrations")

    args = parser.parse_args()

    if args.command == "migrate":
        applied = migrate()
        if applied:
            print(f"applied {len(applied)} migration(s): {', '.join(applied)}")
        else:
            print("database already up to date")


if __name__ == "__main__":
    main()
