"""Decide which posts are worth answering, without calling any API."""

from __future__ import annotations

import re
import time

from .config import Settings
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


def evaluate(post: Post, settings: Settings, *, now: float | None = None) -> tuple[bool, str]:
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

    excluded = _contains_any(text, settings.exclude_keywords)
    if excluded:
        return False, f"matched exclude keyword '{excluded}'"

    if len(post.body.strip()) < settings.min_body_chars:
        return False, f"body too short ({len(post.body.strip())} chars)"

    matched = _contains_any(text, settings.keywords)
    if not matched:
        return False, "no pest keyword matched"

    return True, f"matched keyword '{matched}'"
