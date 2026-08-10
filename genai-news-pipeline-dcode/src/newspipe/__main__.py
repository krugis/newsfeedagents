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


def cmd_label(args: argparse.Namespace) -> None:
    from newspipe.labeling.labeler import run_label

    stats = run_label(limit=args.limit)
    if stats.get("skipped"):
        print("labeling skipped: ANTHROPIC_API_KEY not set")
        return
    print(f"selected: {stats['selected']}, labeled: {stats['labeled']}, failed: {stats['failed']}")
    titles = {c.story_id: c.title for c in stats["contexts"]}
    print(f"{'title':<62}{'is_hot':>7}{'importance':>11}{'category':>20}")
    for story_id, label in stats["results"].items():
        title = titles.get(story_id, f"story {story_id}")
        print(
            f"{title[:60]:<62}{str(label.is_hot):>7}{label.importance:>11}{label.category:>20}"
        )


def cmd_run(args: argparse.Namespace) -> None:
    from newspipe.graph.build import build_graph, hour_thread_id
    from newspipe.graph.state import INITIAL_STATE

    graph = build_graph()
    thread_id = args.resume or args.thread or hour_thread_id()
    # Resuming passes None as input so LangGraph continues from the checkpoint
    # instead of restarting; a fresh run seeds the initial state.
    inp = None if args.resume else INITIAL_STATE
    result = graph.invoke(inp, config={"configurable": {"thread_id": thread_id}})

    stats = result.get("stats", {})
    print(f"thread_id: {thread_id}")
    status = stats.get("status", "?")
    print(f"status:    {status}  (duration {stats.get('duration_seconds', '?')}s)")
    print(f"{'source':<34}{'fetched':>9}{'inserted':>10}  error")
    for name, s in sorted((stats.get("sources") or {}).items()):
        print(f"{name:<34}{s.get('fetched', 0):>9}{s.get('inserted', 0):>10}  {s.get('error', '')}")
    print(
        f"new stories: {stats.get('new_stories', 0)}, updated: {stats.get('stories_updated', 0)}, "
        f"labeled: {stats.get('labeled', 0)}, errors: {stats.get('error_count', 0)}"
    )
    print(
        "fetch executions: "
        f"{len(result.get('fetch_results', []))}, "
        f"new arrival ids: {len(result.get('new_arrival_ids', []))}"
    )
    if result.get("errors"):
        print("\nerrors:")
        for err in result["errors"]:
            print(f"  {err}")


def cmd_status(_args: argparse.Namespace) -> None:
    from newspipe.status import print_status

    print_status()


def cmd_schedule(_args: argparse.Namespace) -> None:
    from newspipe.scheduler import main as scheduler_main

    scheduler_main()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="newspipe", description="GenAI/ML news ingestion pipeline"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="Apply pending DB migrations")
    subparsers.add_parser("fetch", help="Run all due fetchers once")
    subparsers.add_parser("dedup", help="Storify all unattached arrivals")
    label_parser = subparsers.add_parser("label", help="LLM-label unlabeled stories")
    label_parser.add_argument("--limit", type=int, default=None, help="max stories to label")
    run_parser = subparsers.add_parser("run", help="Run the LangGraph pipeline once")
    run_parser.add_argument(
        "--thread",
        type=str,
        default=None,
        help="thread_id to start a fresh run on (default: this hour's slot)",
    )
    run_parser.add_argument(
        "--resume",
        type=str,
        default=None,
        help="thread_id to resume from a checkpoint (mutually exclusive with --thread)",
    )
    subparsers.add_parser("schedule", help="Run the hourly APScheduler (blocking)")
    subparsers.add_parser("status", help="Show last runs, backlog, per-source status")

    args = parser.parse_args()
    if args.command == "migrate":
        cmd_migrate(args)
    elif args.command == "fetch":
        cmd_fetch(args)
    elif args.command == "dedup":
        cmd_dedup(args)
    elif args.command == "label":
        cmd_label(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "schedule":
        cmd_schedule(args)
    elif args.command == "status":
        cmd_status(args)


if __name__ == "__main__":
    main()
