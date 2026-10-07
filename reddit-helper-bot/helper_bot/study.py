"""Collect real, upvoted human replies from each niche's subreddits.

The bot uses these as examples of the register it should write in. Run
`helper-bot study` once before the first scan. It writes one file per niche
under samples/ and every later scan reuses them.
"""

from __future__ import annotations

import logging
import re
import statistics
from pathlib import Path

import yaml

from .config import Niche, Settings
from .models import Post
from .reddit_client import RedditClient

log = logging.getLogger(__name__)

MIN_REPLY_CHARS = 60
MAX_REPLY_CHARS = 900
MIN_SCORE = 3
BOT_AUTHORS = {"automoderator"}
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)


def looks_like_question(post: Post, niche: Niche) -> bool:
    text = f"{post.title} {post.body}".lower()
    if not post.is_self or len(post.body.strip()) < 20:
        return False
    if "?" not in post.title and "?" not in post.body:
        return False
    return any(k.lower() in text for k in niche.keywords)


def usable_reply(comment: dict) -> bool:
    body = comment["body"].strip()
    author = (comment["author"] or "").lower()
    if not author or author in BOT_AUTHORS or "bot" in author:
        return False
    if comment["is_op"]:
        return False
    if body in {"[deleted]", "[removed]"}:
        return False
    if comment["score"] < MIN_SCORE:
        return False
    if not (MIN_REPLY_CHARS <= len(body) <= MAX_REPLY_CHARS):
        return False
    if _URL.search(body):
        return False
    return True


def collect(client: RedditClient, niche: Niche, *, subreddits: list[str] | None = None, posts_per_subreddit: int = 40, replies_per_post: int = 2, time_filter: str = "year") -> list[dict]:
    """One-time collection for a niche. Pools replies from its subreddits into one list."""
    samples: list[dict] = []
    seen_posts: set[str] = set()
    for name in subreddits or niche.subreddits:
        log.info("[%s] studying r/%s", niche.name, name)
        try:
            posts = list(client.top_posts(name, limit=posts_per_subreddit, time_filter=time_filter))
        except Exception as exc:  # noqa: BLE001 - keep going with the next subreddit
            log.warning("skipping r/%s: %s", name, exc)
            continue
        for post in posts:
            if post.id in seen_posts or not looks_like_question(post, niche):
                continue
            seen_posts.add(post.id)
            try:
                comments = client.top_level_comments(post.id, limit=15)
            except Exception as exc:  # noqa: BLE001
                log.warning("skipping %s: %s", post.id, exc)
                continue
            kept = [c for c in comments if usable_reply(c)][:replies_per_post]
            for c in kept:
                samples.append({
                    "subreddit": post.subreddit,
                    "title": post.title.strip(),
                    "question": post.body.strip()[:600],
                    "reply": c["body"].strip(),
                    "score": c["score"],
                    "source": f"https://www.reddit.com/comments/{post.id}/_/{c['id']}",
                })
    samples.sort(key=lambda s: s["score"], reverse=True)
    return samples


def summarize(samples: list[dict]) -> dict:
    """Simple descriptive stats so the user can see what 'human' looks like here."""
    if not samples:
        return {"count": 0}
    lengths = [len(s["reply"]) for s in samples]
    sentences = [max(1, len(re.findall(r"[.!?](\s|$)", s["reply"]))) for s in samples]
    with_lists = sum(1 for s in samples if re.search(r"^\s*([-*]|\d+[.)])\s", s["reply"], re.M))
    lowercase_start = sum(1 for s in samples if s["reply"][:1].islower())
    return {
        "count": len(samples),
        "median_chars": int(statistics.median(lengths)),
        "median_sentences": int(statistics.median(sentences)),
        "percent_with_lists": round(100 * with_lists / len(samples)),
        "percent_lowercase_start": round(100 * lowercase_start / len(samples)),
    }


def save(samples: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Real upvoted replies collected by `helper-bot study`. Used as style examples.\n"
        + yaml.safe_dump({"samples": samples}, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )


def load(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return list(data.get("samples") or [])


def load_all(settings: Settings, niches: list[Niche] | None = None) -> dict[str, list[dict]]:
    """Samples for every (or the given) niche, keyed by niche name."""
    return {n.name: load(n.samples_path) for n in (niches or settings.active_niches())}
