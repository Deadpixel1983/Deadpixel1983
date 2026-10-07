import time

from pest_bot import filters
from tests.conftest import make_post


def test_matching_post_is_candidate(settings):
    ok, reason = filters.evaluate(make_post(), settings)
    assert ok
    assert "ant" in reason


def test_keyword_matches_whole_words_only(settings):
    # "wanted" must not match keyword "ant"; "pants" must not match either.
    post = make_post(title="Wanted: advice on my pants closet smell", body="Nothing in here is about bugs, just a smell in the closet that will not go away.")
    ok, reason = filters.evaluate(post, settings)
    assert not ok
    assert reason == "no pest keyword matched"


def test_keyword_plural_matches(settings):
    post = make_post(title="So many roaches", body="Found three roaches under the sink tonight, what is the move here?")
    assert filters.evaluate(post, settings)[0]


def test_phrase_keyword(settings):
    post = make_post(title="Help", body="Think I have bed bugs, bites in a line on my arm and small dark spots on the sheets.")
    assert filters.evaluate(post, settings)[0]


def test_old_post_skipped(settings):
    post = make_post(created_utc=time.time() - 3600 * 48)
    ok, reason = filters.evaluate(post, settings)
    assert not ok and "old" in reason


def test_link_post_skipped(settings):
    ok, reason = filters.evaluate(make_post(is_self=False), settings)
    assert not ok and reason == "not a text post"


def test_busy_post_skipped(settings):
    ok, reason = filters.evaluate(make_post(num_comments=50), settings)
    assert not ok and "comments" in reason


def test_exclude_keyword(settings):
    post = make_post(title="[META] ants discussion thread", body="Let's talk about ants in general, post your ant stories here.")
    ok, reason = filters.evaluate(post, settings)
    assert not ok and "[meta]" in reason


def test_short_body_skipped(settings):
    ok, reason = filters.evaluate(make_post(body="ants?"), settings)
    assert not ok and "short" in reason


def test_locked_post_skipped(settings):
    ok, reason = filters.evaluate(make_post(locked=True), settings)
    assert not ok
