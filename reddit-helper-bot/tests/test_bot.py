import time

import pytest

from helper_bot import store as st
from helper_bot.answerer import Draft
from helper_bot.bot import Bot, RateLimiter
from helper_bot.reddit_client import RedditError
from tests.conftest import make_post


class FakeReddit:
    def __init__(self, username, posts_by_sub=None, *, replied=False, fail_reply=False):
        self.username = username
        self.posts_by_sub = posts_by_sub or {}
        self.replied = replied
        self.fail_reply = fail_reply
        self.replies = []
        self.fetched = []

    def new_posts(self, subreddit, limit):
        self.fetched.append(subreddit)
        return iter(self.posts_by_sub.get(subreddit, []))

    def refresh(self, post_id):
        for posts in self.posts_by_sub.values():
            for p in posts:
                if p.id == post_id:
                    return p
        return make_post(id=post_id)

    def already_replied(self, post_id):
        return self.replied

    def reply(self, post_id, body):
        if self.fail_reply:
            raise RedditError("boom")
        self.replies.append((post_id, body))
        return f"c_{post_id}"


class FakeAnswerer:
    def __init__(self, reply="use bait not spray.", skip=False):
        self.reply = reply
        self.skip = skip
        self.calls = []

    def draft(self, post, niche):
        self.calls.append((post.id, niche.name))
        if self.skip:
            return Draft(reply=None, skip_reason="not a question", model="m")
        return Draft(reply=self.reply, skip_reason=None, model="m")


def clients(pest_posts=None, food_posts=None, **kw):
    pest = FakeReddit("pestbot", {"pestcontrol": pest_posts or [], "homeowners": pest_posts or []}, **kw)
    food = FakeReddit("foodbot", {"foodsafety": food_posts or []}, **kw)
    return {"pest": pest, "food": food}


def test_bot_requires_a_client_per_account(settings):
    with pytest.raises(ValueError):
        Bot(settings, st.Store(), {"pest": FakeReddit("x")}, FakeAnswerer())


def test_scan_drafts_pending_in_approval_mode(settings):
    settings.approval_mode = True
    cl = clients(pest_posts=[make_post(id="p1"), make_post(id="p2", is_self=False)])
    bot = Bot(settings, st.Store(), cl, FakeAnswerer())
    r = bot.scan()
    assert (r.seen, r.drafted, r.skipped_filter, r.posted) == (2, 1, 1, 0)
    rec = bot.store.get("p1")
    assert rec.status == st.PENDING and rec.niche == "pest_control" and rec.account == "pest"
    assert cl["pest"].replies == []
    # Shared subreddit fetched once, and the second scan does not re-process.
    assert cl["pest"].fetched.count("homeowners") == 1
    assert bot.scan().seen == 0


def test_posts_routed_to_the_right_niche_and_account(settings):
    settings.approval_mode = False
    settings.min_seconds_between_comments = 0
    rice = make_post(id="f1", subreddit="foodsafety", title="Rice left out overnight", body="Left out a pot of rice overnight, is it safe to eat if I reheat it well?")
    cl = clients(pest_posts=[make_post(id="p1")], food_posts=[rice])
    answerer = FakeAnswerer()
    bot = Bot(settings, st.Store(), cl, answerer)
    r = bot.scan()
    assert r.posted == 2
    assert sorted(answerer.calls) == [("f1", "food_hygiene"), ("p1", "pest_control")]
    assert [pid for pid, _ in cl["pest"].replies] == ["p1"]
    assert [pid for pid, _ in cl["food"].replies] == ["f1"]
    assert cl["food"].replies[0][1].endswith("^(bot run by u/foodbot)")
    assert cl["pest"].replies[0][1].endswith("^(bot run by u/pestbot)")


def test_niche_footer_override(settings, niche):
    niche.disclosure_footer = "^(custom {username})"
    bot = Bot(settings, st.Store(), clients(), FakeAnswerer())
    assert bot.render_comment("hi", niche, "pestbot") == "hi\n\n^(custom pestbot)"
    settings.disclosure_footer = ""
    niche.disclosure_footer = None
    assert bot.render_comment("  hi  ", niche, "pestbot") == "hi"


def test_model_skip_is_recorded(settings):
    cl = clients(pest_posts=[make_post(id="p1")])
    bot = Bot(settings, st.Store(), cl, FakeAnswerer(skip=True))
    r = bot.scan()
    assert r.skipped_model == 1
    rec = bot.store.get("p1")
    assert rec.reason == "model: not a question" and rec.niche == "pest_control"


def test_answerer_failure_does_not_mark_seen(settings):
    class Boom:
        def draft(self, post, niche):
            raise RuntimeError("api down")

    bot = Bot(settings, st.Store(), clients(pest_posts=[make_post(id="p1")]), Boom())
    r = bot.scan()
    assert r.errors and not bot.store.has_seen("p1")


def test_rate_limit_is_per_account(settings):
    settings.approval_mode = True
    settings.max_comments_per_hour = 1
    settings.min_seconds_between_comments = 0
    store = st.Store()
    cl = clients()
    bot = Bot(settings, store, cl, FakeAnswerer())
    store.record_draft("p1", "pestcontrol", "t", "u", "d", niche="pest_control", account="pest", status=st.APPROVED)
    store.record_draft("p2", "pestcontrol", "t", "u", "d", niche="pest_control", account="pest", status=st.APPROVED)
    store.record_draft("f1", "foodsafety", "t", "u", "d", niche="food_hygiene", account="food", status=st.APPROVED)
    r = bot.post_approved()
    # One per account goes out; the second pest one is held.
    assert r.posted == 2 and r.held_rate_limit == 1
    assert store.get("p2").status == st.APPROVED
    assert store.get("f1").status == st.POSTED


def test_post_only_selected_niches(settings, niche):
    settings.min_seconds_between_comments = 0
    store = st.Store()
    bot = Bot(settings, store, clients(), FakeAnswerer(), niches=[niche])
    store.record_draft("p1", "pestcontrol", "t", "u", "d", niche="pest_control", account="pest", status=st.APPROVED)
    store.record_draft("f1", "foodsafety", "t", "u", "d", niche="food_hygiene", account="food", status=st.APPROVED)
    r = bot.post_approved()
    assert r.posted == 1 and store.get("f1").status == st.APPROVED


def test_post_skips_closed_or_already_replied(settings):
    settings.min_seconds_between_comments = 0
    store = st.Store()
    cl = clients(pest_posts=[make_post(id="p1", locked=True), make_post(id="p2")], replied=True)
    bot = Bot(settings, store, cl, FakeAnswerer())
    for pid in ("p1", "p2"):
        store.record_draft(pid, "pestcontrol", "t", "u", "d", niche="pest_control", account="pest", status=st.APPROVED)
    r = bot.post_approved()
    assert r.skipped == 2 and cl["pest"].replies == []
    assert "closed" in store.get("p1").reason
    assert "already replied" in store.get("p2").reason


def test_post_failure_marked_failed(settings):
    store = st.Store()
    bot = Bot(settings, store, clients(fail_reply=True), FakeAnswerer())
    store.record_draft("p1", "pestcontrol", "t", "u", "d", niche="pest_control", account="pest", status=st.APPROVED)
    r = bot.post_approved()
    assert r.failed == 1 and store.get("p1").status == st.FAILED


def test_post_with_unknown_niche_fails_cleanly(settings):
    store = st.Store()
    bot = Bot(settings, store, clients(), FakeAnswerer())
    store.record_draft("p1", "pestcontrol", "t", "u", "d", niche="pest_control", account="pest", status=st.APPROVED)
    del settings.niches["pest_control"]
    r = bot.post_approved()
    rec = store.get("p1")
    assert r.posted == 0 and r.failed == 1
    assert rec.status == st.FAILED and "no longer in config" in rec.reason


def test_dry_run_never_posts(settings):
    store = st.Store()
    cl = clients()
    bot = Bot(settings, store, cl, FakeAnswerer(), dry_run=True)
    store.record_draft("p1", "pestcontrol", "t", "u", "d", niche="pest_control", account="pest", status=st.APPROVED)
    r = bot.post_approved()
    assert r.posted == 1 and cl["pest"].replies == []
    assert store.get("p1").status == st.APPROVED


def test_rate_limiter_spacing(settings):
    store = st.Store()
    settings.min_seconds_between_comments = 600
    settings.max_comments_per_hour = 5
    limiter = RateLimiter(settings, store)
    now = time.time()
    assert limiter.wait_seconds("pest", now) == 0
    store.record_draft("p1", "x", "t", "u", "d", account="pest", status=st.APPROVED)
    store.record_posted("p1", "c1", posted_at=now - 100)
    assert 499 < limiter.wait_seconds("pest", now) <= 500
    assert limiter.wait_seconds("food", now) == 0
