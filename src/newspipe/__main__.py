"""CLI entry point: ``python -m newspipe <command>``."""

from __future__ import annotations

import argparse


def cmd_migrate(_args: argparse.Namespace) -> None:
    from newspipe.db.migrate import migrate

    applied = migrate()
    if applied:
        print(f"applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("database already up to date")


def cmd_fetch(_args: argparse.Namespace) -> None:
    from newspipe.fetch import run_fetch

    stats = run_fetch()
    sources = stats["sources"]
    if not sources:
        print("no sources due")
        return
    print(f"{'source':<34}{'fetched':>9}{'inserted':>10}  error")
    for name in sorted(sources):
        s = sources[name]
        print(f"{name:<34}{s['fetched']:>9}{s['inserted']:>10}  {s.get('error', '')}")
    print(f"total: {stats['total_fetched']} fetched, {stats['total_inserted']} inserted")
    if stats["errors"]:
        print("\nerrors:")
        for name, err in stats["errors"].items():
            print(f"  {name}: {err}")


def cmd_dedup(_args: argparse.Namespace) -> None:
    from newspipe.dedup import run_dedup

    stats = run_dedup()
    print(f"arrivals processed: {stats['arrivals_processed']}")
    print(f"stories created:    {stats['stories_created']}")
    print(f"stories updated:    {stats['stories_updated']}")
    print(f"arrivals attached:  {stats['attachments']}")
    if stats["errors"]:
        print(f"errors:             {stats['errors']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="newspipe", description="GenAI/ML news ingestion pipeline"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="Apply pending DB migrations")
    subparsers.add_parser("fetch", help="Run all due fetchers once")
    subparsers.add_parser("dedup", help="Storify all unattached arrivals")

    args = parser.parse_args()
    if args.command == "migrate":
        cmd_migrate(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "dedup":
        cmd_dedup(args)


if __name__ == "__main__":
    main()
