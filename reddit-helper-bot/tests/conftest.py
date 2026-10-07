import time

import pytest

from helper_bot.config import Niche, Settings
from helper_bot.models import Post

PEST_PERSONA = "You know bugs. Never give pesticide dosages."
FOOD_PERSONA = "You know food safety. Two hour rule."


def make_niche(name="pest_control", **overrides) -> Niche:
    base = dict(
        name=name,
        subreddits=["pestcontrol", "homeowners"],
        keywords=["ant", "roach", "bed bug", "mouse", "mice", "termite", "pest control"],
        exclude_keywords=["[meta]", "hiring", "my company"],
        persona=PEST_PERSONA,
        account="pest",
        samples_path=f"samples/{name}.yaml",
    )
    base.update(overrides)
    return Niche(**base)


@pytest.fixture
def niche() -> Niche:
    return make_niche()


@pytest.fixture
def food_niche() -> Niche:
    return make_niche(
        "food_hygiene",
        subreddits=["foodsafety", "homeowners"],
        keywords=["left out", "safe to eat", "mold"],
        exclude_keywords=[],
        persona=FOOD_PERSONA,
        account="food",
    )


@pytest.fixture
def settings(niche, food_niche) -> Settings:
    s = Settings(
        niches={niche.name: niche, food_niche.name: food_niche},
        min_body_chars=20,
        disclosure_footer="^(bot run by u/{username})",
    )
    s.validate()
    return s


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
