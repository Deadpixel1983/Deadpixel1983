import time

import pytest

from pest_bot.config import Settings
from pest_bot.models import Post


@pytest.fixture
def settings() -> Settings:
    return Settings(
        subreddits=["pestcontrol"],
        keywords=["ant", "roach", "bed bug", "mouse", "mice", "termite", "pest control"],
        exclude_keywords=["[meta]", "hiring", "my company"],
        min_body_chars=20,
        disclosure_footer="^(bot run by u/{username})",
    )


def make_post(**overrides) -> Post:
    base = dict(
        id="abc123",
        subreddit="pestcontrol",
        title="Ants in my kitchen, what do I do?",
        body="Little black ants keep showing up on the counter every morning. Tried spraying, they come back.",
        author="someone",
        url="https://www.reddit.com/r/pestcontrol/comments/abc123/x/",
        created_utc=time.time() - 600,
        num_comments=1,
        is_self=True,
    )
    base.update(overrides)
    return Post(**base)
