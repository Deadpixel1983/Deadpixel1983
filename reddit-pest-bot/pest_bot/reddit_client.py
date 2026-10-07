"""Thin wrapper around PRAW so the rest of the bot never touches PRAW objects."""

from __future__ import annotations

import logging
from typing import Iterator

import praw
import prawcore
from praw.exceptions import RedditAPIException

from . import __version__
from .config import Credentials
from .models import Post

log = logging.getLogger(__name__)


class RedditError(Exception):
    """A Reddit API call failed in a way the caller should report."""


class RedditClient:
    def __init__(self, creds: Credentials, *, reddit: praw.Reddit | None = None):
        self.username = creds.reddit_username
        self.reddit = reddit or praw.Reddit(
            client_id=creds.reddit_client_id,
            client_secret=creds.reddit_client_secret,
            username=creds.reddit_username,
            password=creds.reddit_password,
            # Reddit asks that bots identify themselves in the user agent.
            user_agent=f"python:pest-help-bot:v{__version__} (by u/{creds.reddit_username})",
            ratelimit_seconds=300,
        )

    def whoami(self) -> str:
        try:
            me = self.reddit.user.me()
        except (prawcore.PrawcoreException, RedditAPIException) as exc:
            raise RedditError(f"Reddit login failed: {exc}") from exc
        if me is None:
            raise RedditError("Reddit login failed: no user returned (check credentials)")
        return me.name

    def new_posts(self, subreddit: str, limit: int) -> Iterator[Post]:
        try:
            for sub in self.reddit.subreddit(subreddit).new(limit=limit):
                yield to_post(sub)
        except (prawcore.PrawcoreException, RedditAPIException) as exc:
            raise RedditError(f"could not read r/{subreddit}: {exc}") from exc

    def top_posts(self, subreddit: str, limit: int, time_filter: str = "year") -> Iterator[Post]:
        try:
            for sub in self.reddit.subreddit(subreddit).top(time_filter=time_filter, limit=limit):
                yield to_post(sub)
        except (prawcore.PrawcoreException, RedditAPIException) as exc:
            raise RedditError(f"could not read r/{subreddit}: {exc}") from exc

    def top_level_comments(self, post_id: str, limit: int = 30) -> list[dict]:
        """Return top-level comments on a post as plain dicts, highest score first."""
        try:
            submission = self.reddit.submission(id=post_id)
            submission.comment_sort = "top"
            submission.comments.replace_more(limit=0)
            out = []
            for c in submission.comments[:limit]:
                author = getattr(c.author, "name", None)
                out.append({
                    "id": c.id,
                    "author": author,
                    "body": c.body or "",
                    "score": int(getattr(c, "score", 0) or 0),
                    "is_op": bool(getattr(c, "is_submitter", False)),
                })
            return out
        except (prawcore.PrawcoreException, RedditAPIException) as exc:
            raise RedditError(f"could not read comments on {post_id}: {exc}") from exc

    def refresh(self, post_id: str) -> Post:
        """Re-fetch a post so we can check it is still open before replying."""
        try:
            sub = self.reddit.submission(id=post_id)
            sub._fetch()
            return to_post(sub)
        except (prawcore.PrawcoreException, RedditAPIException) as exc:
            raise RedditError(f"could not refresh {post_id}: {exc}") from exc

    def already_replied(self, post_id: str) -> bool:
        """True if our account already has a top-level comment on this post."""
        for c in self.top_level_comments(post_id, limit=200):
            if c["author"] and c["author"].lower() == self.username.lower():
                return True
        return False

    def reply(self, post_id: str, body: str) -> str:
        """Post a top-level comment. Returns the new comment id."""
        try:
            comment = self.reddit.submission(id=post_id).reply(body)
        except RedditAPIException as exc:
            raise RedditError(f"Reddit rejected the comment: {exc}") from exc
        except prawcore.PrawcoreException as exc:
            raise RedditError(f"posting failed: {exc}") from exc
        if comment is None:
            raise RedditError("Reddit accepted the comment but returned nothing (possible shadow filter)")
        return comment.id


def to_post(sub) -> Post:
    """Convert a PRAW Submission (or anything duck-typed like one) into a Post."""
    author = getattr(sub.author, "name", None) or "[deleted]"
    removed = bool(getattr(sub, "removed_by_category", None)) or author == "[deleted]"
    return Post(
        id=sub.id,
        subreddit=str(sub.subreddit),
        title=sub.title or "",
        body=sub.selftext or "",
        author=author,
        url=f"https://www.reddit.com{sub.permalink}",
        created_utc=float(sub.created_utc),
        num_comments=int(sub.num_comments or 0),
        is_self=bool(sub.is_self),
        locked=bool(getattr(sub, "locked", False)),
        archived=bool(getattr(sub, "archived", False)),
        removed=removed,
    )
