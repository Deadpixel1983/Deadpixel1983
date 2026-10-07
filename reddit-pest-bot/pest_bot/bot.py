"""Orchestration: scan subreddits, draft replies, approve, post."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import filters, store as store_mod
from .answerer import Answerer
from .config import Settings
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
    """Enforces the per-hour and spacing limits using the store's comment log."""

    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store

    def wait_seconds(self, now: float | None = None) -> float:
        """0 if we may post now, else how long until we may."""
        now = time.time() if now is None else now
        waits = [0.0]
        last = self.store.last_comment_time()
        if last is not None:
            waits.append(last + self.settings.min_seconds_between_comments - now)
        if self.store.comments_since(now - 3600) >= self.settings.max_comments_per_hour:
            # Conservative: wait until the oldest comment in the window ages out.
            waits.append(3600.0)
        return max(waits)


class Bot:
    def __init__(self, settings: Settings, store: Store, reddit: RedditClient, answerer: Answerer, *, dry_run: bool = False):
        self.settings = settings
        self.store = store
        self.reddit = reddit
        self.answerer = answerer
        self.dry_run = dry_run
        self.limiter = RateLimiter(settings, store)

    # -- scan ----------------------------------------------------------------

    def scan(self) -> ScanReport:
        """Look at new posts, draft replies for candidates, post if not in approval mode."""
        report = ScanReport()
        for name in self.settings.subreddits:
            try:
                posts = list(self.reddit.new_posts(name, limit=self.settings.posts_per_subreddit))
            except RedditError as exc:
                log.warning("%s", exc)
                report.errors.append(str(exc))
                continue
            for post in posts:
                self._consider(post, report)
        return report

    def _consider(self, post: Post, report: ScanReport) -> None:
        if self.store.has_seen(post.id):
            return
        report.seen += 1

        ok, reason = filters.evaluate(post, self.settings)
        if not ok:
            log.debug("skip %s: %s", post.id, reason)
            self.store.record_skip(post.id, post.subreddit, post.title, post.url, reason)
            report.skipped_filter += 1
            return

        log.info("candidate %s in r/%s: %s (%s)", post.id, post.subreddit, post.title[:70], reason)
        try:
            draft = self.answerer.draft(post)
        except Exception as exc:  # noqa: BLE001 - log and move on; do not mark as seen
            log.error("drafting failed for %s: %s", post.id, exc)
            report.errors.append(f"{post.id}: {exc}")
            return

        if draft.skipped:
            log.info("model skipped %s: %s", post.id, draft.skip_reason)
            self.store.record_skip(post.id, post.subreddit, post.title, post.url, f"model: {draft.skip_reason}")
            report.skipped_model += 1
            return

        status = store_mod.PENDING if self.settings.approval_mode else store_mod.APPROVED
        self.store.record_draft(post.id, post.subreddit, post.title, post.url, draft.reply, status=status)
        report.drafted += 1
        log.info("drafted reply for %s (%d in, %d out tokens)", post.id, draft.input_tokens, draft.output_tokens)

        if not self.settings.approval_mode:
            result = self.post_approved(limit=1)
            report.posted += result.posted
            report.errors.extend(result.errors)

    # -- post ----------------------------------------------------------------

    def post_approved(self, limit: int | None = None) -> PostReport:
        """Post approved drafts, oldest first, respecting rate limits."""
        report = PostReport()
        approved = self.store.list_by_status(store_mod.APPROVED)
        if limit is not None:
            approved = approved[:limit]

        for rec in approved:
            wait = self.limiter.wait_seconds()
            if wait > 0 and not self.dry_run:
                log.info("rate limit: next post allowed in %d s; holding %d approved", int(wait), len(approved) - report.posted)
                report.held_rate_limit += len(approved) - report.posted - report.skipped - report.failed
                break
            self._post_one(rec, report)
        return report

    def _post_one(self, rec: store_mod.Record, report: PostReport) -> None:
        body = self.render_comment(rec.draft or "")
        if self.dry_run:
            log.info("[dry-run] would post to %s:\n%s\n", rec.url, body)
            report.posted += 1
            return
        try:
            fresh = self.reddit.refresh(rec.id)
            if fresh.locked or fresh.archived or fresh.removed:
                self.store.set_status(rec.id, store_mod.SKIPPED, reason="post closed before we could reply")
                report.skipped += 1
                return
            if self.reddit.already_replied(rec.id):
                self.store.set_status(rec.id, store_mod.SKIPPED, reason="we already replied on this post")
                report.skipped += 1
                return
            comment_id = self.reddit.reply(rec.id, body)
        except RedditError as exc:
            log.error("posting to %s failed: %s", rec.id, exc)
            self.store.set_status(rec.id, store_mod.FAILED, reason=str(exc))
            report.failed += 1
            report.errors.append(f"{rec.id}: {exc}")
            return
        self.store.record_posted(rec.id, comment_id)
        report.posted += 1
        log.info("posted comment %s on %s", comment_id, rec.url)

    def render_comment(self, draft: str) -> str:
        footer = self.settings.disclosure_footer.strip()
        if not footer:
            return draft.strip()
        footer = footer.replace("{username}", self.reddit.username)
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
