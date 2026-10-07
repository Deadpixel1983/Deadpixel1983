"""Command-line entry point: helper-bot <command>."""

from __future__ import annotations

import argparse
import logging
import sys
import textwrap

from . import store as store_mod, study
from .answerer import Answerer
from .bot import Bot
from .config import ConfigError, load_all_credentials, load_settings
from .reddit_client import RedditClient, RedditError, mask_proxy
from .store import Store


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="helper-bot", description="Reddit helper bot for one or more niches.")
    p.add_argument("--config", default="config.yaml", help="path to config.yaml")
    p.add_argument("--env", default=".env", help="path to .env with credentials")
    p.add_argument("--niche", action="append", default=None, help="restrict to this niche (repeatable)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")

    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("check", help="verify credentials and config for every account, then exit")
    s.add_argument("--show-ip", action="store_true", help="also print the public IP each account exits from")

    s = sub.add_parser("study", help="one-time: collect upvoted human replies per niche as style examples")
    s.add_argument("--posts", type=int, default=40, help="top posts per subreddit to look at")
    s.add_argument("--per-post", type=int, default=2, help="replies to keep per post")
    s.add_argument("--time", default="year", choices=["month", "year", "all"], help="top-posts time window")
    s.add_argument("--subreddits", default=None, help="comma-separated list to study instead of the niche's own")

    s = sub.add_parser("scan", help="fetch new posts and draft replies once")
    s.add_argument("--dry-run", action="store_true", help="never post, only print what would be posted")

    s = sub.add_parser("review", help="approve, edit, or reject pending drafts")
    s.add_argument("--all", action="store_true", help="approve everything without prompting")

    s = sub.add_parser("post", help="post approved drafts (respects per-account rate limits)")
    s.add_argument("--dry-run", action="store_true", help="print instead of posting")
    s.add_argument("--drain", action="store_true", help="keep going, waiting out the rate limit, until nothing approved is left")

    s = sub.add_parser("run", help="scan on a loop, once a day by default; in approval mode this only drafts")
    s.add_argument("--interval", type=int, default=86400, help="seconds between scans (default: one day)")
    s.add_argument("--dry-run", action="store_true", help="never post")

    sub.add_parser("status", help="show counts by niche and status")

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
    niches = settings.active_niches(args.niche)
    niche_names = {n.name for n in niches}

    if args.command == "status":
        with Store(settings.database_path) as st:
            by_niche = st.counts_by_niche()
        statuses = sorted(store_mod.STATUSES)
        print(f"{'niche':<18}" + "".join(f"{s:>10}" for s in statuses))
        for niche, counts in sorted(by_niche.items()):
            if args.niche and niche not in niche_names:
                continue
            print(f"{(niche or '(no niche)'):<18}" + "".join(f"{counts.get(s, 0):>10}" for s in statuses))
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
    creds = load_all_credentials(args.env, settings.accounts(niches), require_anthropic=needs_claude)
    clients = {name: RedditClient(c) for name, c in creds.items()}

    if args.command == "check":
        ips: dict[str, list[str]] = {}
        for name, client in clients.items():
            user = client.whoami()
            used_by = [n.name for n in niches if n.account == name]
            line = f"account '{name}': logged in as u/{user}  proxy {mask_proxy(client.proxy)}  (niches: {', '.join(used_by)})"
            if args.show_ip:
                try:
                    ip = client.egress_ip()
                    ips.setdefault(ip, []).append(name)
                    line += f"  ip {ip}"
                except RedditError as exc:
                    line += f"  ip check failed: {exc}"
            print(line)
        for ip, names in ips.items():
            if len(names) > 1:
                print(f"warning: accounts {', '.join(names)} share the same public IP {ip}")
        any_creds = next(iter(creds.values()))
        print(f"anthropic key: {'set' if any_creds.anthropic_api_key else 'NOT set'}")
        for n in niches:
            count = len(study.load(n.samples_path))
            print(f"niche '{n.name}': {len(n.subreddits)} subreddits, {len(n.keywords)} keywords, {count} style samples in {n.samples_path}")
        print(f"approval mode: {'on' if settings.approval_mode else 'OFF (posts automatically)'}")
        return 0

    if args.command == "study":
        subreddits = [x.strip() for x in args.subreddits.split(",") if x.strip()] if args.subreddits else None
        for n in niches:
            client = clients[n.account]
            client.whoami()
            samples = study.collect(client, n, subreddits=subreddits, posts_per_subreddit=args.posts, replies_per_post=args.per_post, time_filter=args.time)
            study.save(samples, n.samples_path)
            stats = study.summarize(samples)
            print(f"[{n.name}] saved {stats.get('count', 0)} replies to {n.samples_path}")
            if stats.get("count"):
                print(f"    median length {stats['median_chars']} chars, {stats['median_sentences']} sentences; "
                      f"{stats['percent_with_lists']}% use lists; {stats['percent_lowercase_start']}% start lowercase")
        return 0

    with Store(settings.database_path) as st:
        if args.command == "review":
            return _review(st, niche_names if args.niche else None, approve_all=args.all)

        dry_run = getattr(args, "dry_run", False)
        answerer = Answerer(settings, samples=study.load_all(settings, niches)) if needs_claude else None
        bot = Bot(settings, st, clients, answerer, niches=niches, dry_run=dry_run)  # type: ignore[arg-type]

        if args.command == "scan":
            for client in clients.values():
                client.whoami()
            for n in niches:
                if not answerer.samples.get(n.name):
                    logging.getLogger(__name__).warning("[%s] no style samples; run `helper-bot study` first for more natural replies", n.name)
            r = bot.scan()
            print(f"seen {r.seen}, drafted {r.drafted}, posted {r.posted}, "
                  f"skipped by filter {r.skipped_filter}, skipped by model {r.skipped_model}, errors {len(r.errors)}")
            if settings.approval_mode and r.drafted:
                print("run `helper-bot review` to approve drafts, then `helper-bot post`")
            return 0

        if args.command == "post":
            for client in clients.values():
                client.whoami()
            r = bot.drain_approved() if args.drain else bot.post_approved()
            print(f"posted {r.posted}, held for rate limit {r.held_rate_limit}, skipped {r.skipped}, failed {r.failed}")
            return 0 if not r.errors else 1

        if args.command == "run":
            for client in clients.values():
                client.whoami()
            bot.run_forever(args.interval)
            return 0

    return 1


def _print_record(rec: store_mod.Record) -> None:
    print(f"[{rec.status}] [{rec.niche or 'no niche'} / account {rec.account}] r/{rec.subreddit}: {rec.title}")
    print(rec.url)
    if rec.reason:
        print(f"reason: {rec.reason}")
    if rec.draft:
        print()
        print(textwrap.indent(rec.draft, "    "))


def _review(st: Store, niche_names: set[str] | None, *, approve_all: bool) -> int:
    pending = st.list_by_status(store_mod.PENDING)
    if niche_names is not None:
        pending = [r for r in pending if r.niche in niche_names]
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
    print(f"\n{approved} approved, {remaining} still pending. Run `helper-bot post` to publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
