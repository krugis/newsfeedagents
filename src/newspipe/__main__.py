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
    sub.add_parser("dedup", help="deduplicate unattached arrivals into stories")
    label_parser = sub.add_parser("label", help="label unlabeled stories with the LLM")
    label_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="max stories to label this run (default: settings.label_limit_per_run)",
    )

    args = parser.parse_args(argv)

    if args.command == "fetch":
        from newspipe import fetch

        return fetch.main()
    if args.command == "dedup":
        from newspipe import dedup

        return dedup.main()
    if args.command == "label":
        from newspipe.labeling import labeler

        return labeler.main(limit=args.limit)

    parser.error(f"unknown command: {args.command!r}")
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
