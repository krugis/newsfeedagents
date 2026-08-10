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
    run_parser = sub.add_parser(
        "run", help="one full pipeline run (fetch->dedup->label->finalize), checkpointed"
    )
    run_parser.add_argument(
        "--resume",
        metavar="THREAD_ID",
        default=None,
        help="resume this thread (default: this hour's run-YYYYMMDD-HH)",
    )
    sub.add_parser("status", help="show last runs, unlabeled backlog, source health, errors")
    sub.add_parser("scheduler", help="run the hourly scheduler (foreground or via systemd)")

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
    if args.command == "run":
        from newspipe.graph import build

        return build.main(resume=args.resume)
    if args.command == "status":
        from newspipe import status

        return status.main()
    if args.command == "scheduler":
        from newspipe import scheduler

        return scheduler.main()

    parser.error(f"unknown command: {args.command!r}")
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
