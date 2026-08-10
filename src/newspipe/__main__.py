"""Command-line entrypoint: `python -m newspipe <command>`."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="newspipe",
        description="GenAI/ML news ingestion pipeline",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fetch", help="fetch all due sources once (per-source counts)")

    args = parser.parse_args(argv)

    if args.command == "fetch":
        from newspipe import fetch

        return fetch.main()

    parser.error(f"unknown command: {args.command!r}")
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
