"""Command-line entry point: pest-bot <command>."""

from __future__ import annotations

import argparse
import logging
import sys
import textwrap

from . import store as store_mod, study
from .answerer import Answerer
from .bot import Bot
from .config import ConfigError, load_credentials, load_settings
from .reddit_client import RedditClient, RedditError
from .store import Store


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pest-bot", description="Reddit pest-control helper bot.")
    p.add_argument("--config", default="config.yaml", help="path to config.yaml")
    p.add_argument("--env", default=".env", help="path to .env with credentials")
    p.add_argument("--samples", default=study.SAMPLES_PATH, help="path to samples.yaml from `study`")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify credentials and config, then exit")

    s = sub.add_parser("study", help="collect upvoted human replies as style examples")
    s.add_argument("--posts", type=int, default=40, help="top posts per subreddit to look at")
    s.add_argument("--per-post", type=int, default=2, help="replies to keep per post")
    s.add_argument("--time", default="year", choices=["month", "year", "all"], help="top-posts time window")
    s.add_argument("--subreddits", default=None, help="comma-separated list to study instead of the configured ones")

    s = sub.add_parser("scan", help="fetch new posts and draft replies once")
    s.add_argument("--dry-run", action="store_true", help="never post, only print what would be posted")

    s = sub.add_parser("review", help="approve, edit, or reject pending drafts")
    s.add_argument("--all", action="store_true", help="approve everything without prompting")

    s = sub.add_parser("post", help="post approved drafts (respects rate limits)")
    s.add_argument("--dry-run", action="store_true", help="print instead of posting")

    s = sub.add_parser("run", help="scan on a loop; in approval mode this only drafts")
    s.add_argument("--interval", type=int, default=600, help="seconds between scans")
    s.add_argument("--dry-run", action="store_true", help="never post")

    sub.add_parser("status", help="show counts by status")

    s = sub.add_parser("show", help="print one record by post id")
    s.add_argument("post_id")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return _dispatch(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RedditError as exc:
        print(f"reddit error: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


def _dispatch(args) -> int:
    settings = load_settings(args.config)

    if args.command == "status":
        with Store(settings.database_path) as st:
            counts = st.counts()
        for status in sorted(store_mod.STATUSES):
            print(f"{status:>9}: {counts.get(status, 0)}")
        return 0

    if args.command == "show":
        with Store(settings.database_path) as st:
            rec = st.get(args.post_id)
        if rec is None:
            print("not found")
            return 1
        _print_record(rec)
        return 0

    needs_claude = args.command in {"scan", "run"}
    creds = load_credentials(args.env, require_anthropic=needs_claude)
    reddit = RedditClient(creds)

    if args.command == "check":
        name = reddit.whoami()
        print(f"reddit: logged in as u/{name}")
        print(f"anthropic key: {'set' if creds.anthropic_api_key else 'NOT set'}")
        samples = study.load(args.samples)
        print(f"style samples: {len(samples)} loaded from {args.samples}")
        print(f"subreddits: {', '.join(settings.subreddits)}")
        print(f"approval mode: {'on' if settings.approval_mode else 'OFF (posts automatically)'}")
        return 0

    if args.command == "study":
        reddit.whoami()
        subreddits = [x.strip() for x in args.subreddits.split(",") if x.strip()] if args.subreddits else None
        samples = study.collect(reddit, settings, subreddits=subreddits, posts_per_subreddit=args.posts, replies_per_post=args.per_post, time_filter=args.time)
        study.save(samples, args.samples)
        stats = study.summarize(samples)
        print(f"saved {stats.get('count', 0)} replies to {args.samples}")
        if stats.get("count"):
            print(f"median length: {stats['median_chars']} chars, {stats['median_sentences']} sentences")
            print(f"replies using lists: {stats['percent_with_lists']}%")
            print(f"replies starting lowercase: {stats['percent_lowercase_start']}%")
        return 0

    with Store(settings.database_path) as st:
        if args.command == "review":
            return _review(st, settings, reddit, approve_all=args.all)

        dry_run = getattr(args, "dry_run", False)
        samples = study.load(args.samples) if needs_claude else None
        answerer = Answerer(settings, samples=samples) if needs_claude else None
        bot = Bot(settings, st, reddit, answerer, dry_run=dry_run)  # type: ignore[arg-type]

        if args.command == "scan":
            reddit.whoami()
            if needs_claude and not samples:
                logging.getLogger(__name__).warning("no style samples found; run `pest-bot study` first for more natural replies")
            r = bot.scan()
            print(f"seen {r.seen}, drafted {r.drafted}, posted {r.posted}, "
                  f"skipped by filter {r.skipped_filter}, skipped by model {r.skipped_model}, errors {len(r.errors)}")
            if settings.approval_mode and r.drafted:
                print("run `pest-bot review` to approve drafts, then `pest-bot post`")
            return 0

        if args.command == "post":
            reddit.whoami()
            r = bot.post_approved()
            print(f"posted {r.posted}, held for rate limit {r.held_rate_limit}, skipped {r.skipped}, failed {r.failed}")
            return 0 if not r.errors else 1

        if args.command == "run":
            reddit.whoami()
            bot.run_forever(args.interval)
            return 0

    return 1


def _print_record(rec: store_mod.Record) -> None:
    print(f"[{rec.status}] r/{rec.subreddit}: {rec.title}")
    print(rec.url)
    if rec.reason:
        print(f"reason: {rec.reason}")
    if rec.draft:
        print()
        print(textwrap.indent(rec.draft, "    "))


def _review(st: Store, settings, reddit: RedditClient, *, approve_all: bool) -> int:
    pending = st.list_by_status(store_mod.PENDING)
    if not pending:
        print("nothing pending")
        return 0
    if approve_all:
        for rec in pending:
            st.set_status(rec.id, store_mod.APPROVED)
        print(f"approved {len(pending)}")
        return 0

    print(f"{len(pending)} pending. For each: [a]pprove, [e]dit, [r]eject, [s]kip for now, [q]uit")
    for i, rec in enumerate(pending, start=1):
        print(f"\n--- {i}/{len(pending)} ---")
        _print_record(rec)
        while True:
            choice = input("> ").strip().lower()[:1]
            if choice == "a":
                st.set_status(rec.id, store_mod.APPROVED)
                break
            if choice == "r":
                st.set_status(rec.id, store_mod.REJECTED, reason="rejected in review")
                break
            if choice == "s":
                break
            if choice == "q":
                return 0
            if choice == "e":
                print("enter the new reply; finish with a line containing only END")
                lines = []
                while True:
                    line = input()
                    if line.strip() == "END":
                        break
                    lines.append(line)
                new = "\n".join(lines).strip()
                if new:
                    st.set_status(rec.id, store_mod.APPROVED, draft=new)
                    print("saved and approved")
                    break
                print("empty; not changed")
                continue
            print("a / e / r / s / q")
    remaining = len(st.list_by_status(store_mod.PENDING))
    approved = len(st.list_by_status(store_mod.APPROVED))
    print(f"\n{approved} approved, {remaining} still pending. Run `pest-bot post` to publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
