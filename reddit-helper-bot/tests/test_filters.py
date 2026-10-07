import time

from helper_bot import filters
from tests.conftest import make_post


def test_matching_post_is_candidate(settings, niche):
    ok, reason = filters.evaluate(make_post(), settings, niche)
    assert ok
    assert "ant" in reason


def test_keyword_matches_whole_words_only(settings, niche):
    post = make_post(title="Wanted: advice on my pants closet smell", body="Nothing in here is about bugs, just a smell in the closet that will not go away.")
    ok, reason = filters.evaluate(post, settings, niche)
    assert not ok
    assert reason == "no pest_control keyword matched"


def test_keyword_plural_matches(settings, niche):
    post = make_post(title="So many roaches", body="Found three roaches under the sink tonight, what is the move here?")
    assert filters.evaluate(post, settings, niche)[0]


def test_phrase_keyword(settings, niche):
    post = make_post(title="Help", body="Think I have bed bugs, bites in a line on my arm and small dark spots on the sheets.")
    assert filters.evaluate(post, settings, niche)[0]


def test_old_post_skipped(settings, niche):
    post = make_post(created_utc=time.time() - 3600 * 48)
    ok, reason = filters.evaluate(post, settings, niche)
    assert not ok and "old" in reason


def test_link_post_skipped(settings, niche):
    ok, reason = filters.evaluate(make_post(is_self=False), settings, niche)
    assert not ok and reason == "not a text post"


def test_busy_post_skipped(settings, niche):
    ok, reason = filters.evaluate(make_post(num_comments=50), settings, niche)
    assert not ok and "comments" in reason


def test_exclude_keyword(settings, niche):
    post = make_post(title="[META] ants discussion thread", body="Let's talk about ants in general, post your ant stories here.")
    ok, reason = filters.evaluate(post, settings, niche)
    assert not ok and "[meta]" in reason


def test_short_body_skipped(settings, niche):
    ok, reason = filters.evaluate(make_post(body="ants?"), settings, niche)
    assert not ok and "short" in reason


def test_locked_post_skipped(settings, niche):
    ok, reason = filters.evaluate(make_post(locked=True), settings, niche)
    assert not ok


def test_match_niche_picks_first_matching_niche(settings, niche, food_niche):
    # r/homeowners is shared; keyword decides.
    ants = make_post(subreddit="homeowners")
    assert filters.match_niche(ants, settings, [niche, food_niche])[0] is niche
    rice = make_post(subreddit="homeowners", title="Rice left out overnight", body="Left out a pot of rice on the stove overnight, is it safe to eat if I reheat it well?")
    assert filters.match_niche(rice, settings, [niche, food_niche])[0] is food_niche


def test_match_niche_respects_subreddit_membership(settings, niche, food_niche):
    # Ants post in r/foodsafety: pest niche doesn't watch that sub, food niche has no ant keyword.
    post = make_post(subreddit="foodsafety")
    matched, reason = filters.match_niche(post, settings, [niche, food_niche])
    assert matched is None and "food_hygiene" in reason
    nowhere = make_post(subreddit="gardening")
    assert filters.match_niche(nowhere, settings, [niche, food_niche]) == (None, "subreddit not configured for any niche")
