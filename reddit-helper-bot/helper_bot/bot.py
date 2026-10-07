"""Orchestration: scan subreddits per niche, draft replies, approve, post."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import filters, store as store_mod
from .answerer import Answerer
from .config import Niche, Settings
from .models import Post
from .reddit_client import RedditClient, RedditError
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class ScanReport:
    seen: int = 0
    skipped_filter: int = 0
    skipped_model: int = 0
    drafted: int = 0
    posted: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class PostReport:
    posted: int = 0
    held_rate_limit: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


class RateLimiter:
    """Enforces the per-hour and spacing limits per Reddit account."""

    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store

    def wait_seconds(self, account: str, now: float | None = None) -> float:
        """0 if this account may post now, else how long until it may."""
        now = time.time() if now is None else now
        waits = [0.0]
        last = self.store.last_comment_time(account=account)
        if last is not None:
            waits.append(last + self.settings.min_seconds_between_comments - now)
        if self.store.comments_since(now - 3600, account=account) >= self.settings.max_comments_per_hour:
            # Conservative: wait until the oldest comment in the window ages out.
            waits.append(3600.0)
        return max(waits)


class Bot:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        clients: dict[str, RedditClient],
        answerer: Answerer,
        *,
        niches: list[Niche] | None = None,
        dry_run: bool = False,
    ):
        self.settings = settings
        self.store = store
        self.clients = clients
        self.answerer = answerer
        self.niches = niches or settings.active_niches()
        self.dry_run = dry_run
        self.limiter = RateLimiter(settings, store)
        missing = [n.account for n in self.niches if n.account not in clients]
        if missing:
            raise ValueError(f"no Reddit client for account(s): {', '.join(sorted(set(missing)))}")

    def client_for(self, account: str) -> RedditClient:
        return self.clients[account]

    # -- scan ----------------------------------------------------------------

    def scan(self) -> ScanReport:
        """Look at new posts in every niche's subreddits and draft replies for candidates."""
        report = ScanReport()
        # A subreddit may belong to more than one niche; fetch it once and let
        # the niches compete for each post in config order.
        by_subreddit: dict[str, list[Niche]] = {}
        for niche in self.niches:
            for name in niche.subreddits:
                by_subreddit.setdefault(name.lower(), []).append(niche)

        for subreddit, niches in by_subreddit.items():
            client = self.client_for(niches[0].account)
            try:
                posts = list(client.new_posts(subreddit, limit=self.settings.posts_per_subreddit))
            except RedditError as exc:
                log.warning("%s", exc)
                report.errors.append(str(exc))
                continue
            for post in posts:
                self._consider(post, niches, report)
        return report

    def _consider(self, post: Post, niches: list[Niche], report: ScanReport) -> None:
        if self.store.has_seen(post.id):
            return
        report.seen += 1

        niche, reason = filters.match_niche(post, self.settings, niches)
        if niche is None:
            log.debug("skip %s: %s", post.id, reason)
            self.store.record_skip(post.id, post.subreddit, post.title, post.url, reason)
            report.skipped_filter += 1
            return

        log.info("[%s] candidate %s in r/%s: %s (%s)", niche.name, post.id, post.subreddit, post.title[:70], reason)
        try:
            draft = self.answerer.draft(post, niche)
        except Exception as exc:  # noqa: BLE001 - log and move on; do not mark as seen
            log.error("drafting failed for %s: %s", post.id, exc)
            report.errors.append(f"{post.id}: {exc}")
            return

        if draft.skipped:
            log.info("[%s] model skipped %s: %s", niche.name, post.id, draft.skip_reason)
            self.store.record_skip(post.id, post.subreddit, post.title, post.url, f"model: {draft.skip_reason}", niche=niche.name, account=niche.account)
            report.skipped_model += 1
            return

        status = store_mod.PENDING if self.settings.approval_mode else store_mod.APPROVED
        self.store.record_draft(post.id, post.subreddit, post.title, post.url, draft.reply, niche=niche.name, account=niche.account, status=status)
        report.drafted += 1
        log.info("[%s] drafted reply for %s (%d in, %d out tokens)", niche.name, post.id, draft.input_tokens, draft.output_tokens)

        if not self.settings.approval_mode:
            result = self.post_approved(limit=1, niche=niche.name)
            report.posted += result.posted
            report.errors.extend(result.errors)

    # -- post ----------------------------------------------------------------

    def post_approved(self, limit: int | None = None, *, niche: str | None = None) -> PostReport:
        """Post approved drafts, oldest first, respecting each account's rate limits."""
        report = PostReport()
        approved = self.store.list_by_status(store_mod.APPROVED, niche=niche)
        approved = [r for r in approved if r.niche in {n.name for n in self.niches}]
        if limit is not None:
            approved = approved[:limit]

        for rec in approved:
            wait = self.limiter.wait_seconds(rec.account)
            if wait > 0 and not self.dry_run:
                log.info("rate limit on account '%s': next post allowed in %d s; holding %s", rec.account, int(wait), rec.id)
                report.held_rate_limit += 1
                continue
            self._post_one(rec, report)
        return report

    def _post_one(self, rec: store_mod.Record, report: PostReport) -> None:
        niche = self.settings.niches.get(rec.niche)
        if niche is None:
            self.store.set_status(rec.id, store_mod.FAILED, reason=f"niche '{rec.niche}' no longer in config")
            report.failed += 1
            return
        client = self.client_for(rec.account)
        body = self.render_comment(rec.draft or "", niche, client.username)
        if self.dry_run:
            log.info("[dry-run] would post as u/%s to %s:\n%s\n", client.username, rec.url, body)
            report.posted += 1
            return
        try:
            fresh = client.refresh(rec.id)
            if fresh.locked or fresh.archived or fresh.removed:
                self.store.set_status(rec.id, store_mod.SKIPPED, reason="post closed before we could reply")
                report.skipped += 1
                return
            if client.already_replied(rec.id):
                self.store.set_status(rec.id, store_mod.SKIPPED, reason="we already replied on this post")
                report.skipped += 1
                return
            comment_id = client.reply(rec.id, body)
        except RedditError as exc:
            log.error("posting to %s failed: %s", rec.id, exc)
            self.store.set_status(rec.id, store_mod.FAILED, reason=str(exc))
            report.failed += 1
            report.errors.append(f"{rec.id}: {exc}")
            return
        self.store.record_posted(rec.id, comment_id)
        report.posted += 1
        log.info("[%s] posted comment %s as u/%s on %s", rec.niche, comment_id, client.username, rec.url)

    def render_comment(self, draft: str, niche: Niche, username: str) -> str:
        footer = self.settings.footer_for(niche).strip()
        if not footer:
            return draft.strip()
        footer = footer.replace("{username}", username)
        return f"{draft.strip()}\n\n{footer}"

    # -- run loop --------------------------------------------------------------

    def run_forever(self, interval_seconds: int, *, max_iterations: int | None = None) -> None:
        i = 0
        while True:
            i += 1
            try:
                r = self.scan()
                log.info("scan: seen=%d drafted=%d posted=%d skipped(filter=%d, model=%d) errors=%d",
                         r.seen, r.drafted, r.posted, r.skipped_filter, r.skipped_model, len(r.errors))
                if not self.settings.approval_mode:
                    self.post_approved()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                log.exception("scan iteration failed: %s", exc)
            if max_iterations is not None and i >= max_iterations:
                return
            time.sleep(interval_seconds)
