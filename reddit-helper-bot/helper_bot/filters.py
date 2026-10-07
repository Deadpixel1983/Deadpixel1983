"""Decide which posts are worth answering, without calling any API."""

from __future__ import annotations

import re
import time

from .config import Niche, Settings
from .models import Post


def _contains_any(text: str, terms: list[str]) -> str | None:
    """Return the first term found in text as a whole word or phrase, else None."""
    lowered = text.lower()
    for term in terms:
        t = term.lower().strip()
        if not t:
            continue
        # Phrases and bracketed tags are matched as substrings; single words
        # must match on word boundaries so "ant" does not match "wanted".
        if " " in t or not t.isalnum():
            if t in lowered:
                return term
        elif re.search(rf"\b{re.escape(t)}(?:s|es)?\b", lowered):
            return term
    return None


def evaluate(post: Post, settings: Settings, niche: Niche, *, now: float | None = None) -> tuple[bool, str]:
    """Return (is_candidate, reason). The reason is human-readable for logs."""
    now = time.time() if now is None else now

    if post.removed or post.locked or post.archived:
        return False, "post is locked, archived, or removed"

    age_hours = (now - post.created_utc) / 3600
    if age_hours > settings.max_post_age_hours:
        return False, f"post is {age_hours:.1f}h old (limit {settings.max_post_age_hours}h)"

    if settings.self_posts_only and not post.is_self:
        return False, "not a text post"

    if post.num_comments >= settings.skip_if_comments_at_least:
        return False, f"already has {post.num_comments} comments"

    text = f"{post.title}\n{post.body}"

    excluded = _contains_any(text, niche.exclude_keywords)
    if excluded:
        return False, f"matched exclude keyword '{excluded}'"

    if len(post.body.strip()) < settings.min_body_chars:
        return False, f"body too short ({len(post.body.strip())} chars)"

    matched = _contains_any(text, niche.keywords)
    if not matched:
        return False, f"no {niche.name} keyword matched"

    return True, f"matched keyword '{matched}'"


def match_niche(post: Post, settings: Settings, niches: list[Niche], *, now: float | None = None) -> tuple[Niche | None, str]:
    """Pick the first niche (in config order) that accepts the post.

    Returns (niche, reason). If no niche accepts it, niche is None and reason
    explains the last rejection, or the generic one when none applied.
    """
    reasons: list[str] = []
    for niche in niches:
        if post.subreddit.lower() not in {s.lower() for s in niche.subreddits}:
            continue
        ok, reason = evaluate(post, settings, niche, now=now)
        if ok:
            return niche, reason
        reasons.append(reason)
    if not reasons:
        return None, "subreddit not configured for any niche"
    # If every niche gave the same structural reason, report it once.
    return None, "; ".join(dict.fromkeys(reasons))
