"""Plain data types shared across modules, independent of PRAW."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Post:
    """A Reddit submission, reduced to the fields the bot needs."""

    id: str
    subreddit: str
    title: str
    body: str
    author: str
    url: str
    created_utc: float
    num_comments: int
    is_self: bool
    locked: bool = False
    archived: bool = False
    removed: bool = False
