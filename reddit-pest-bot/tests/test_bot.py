import time

from pest_bot import store as st
from pest_bot.answerer import Draft
from pest_bot.bot import Bot, RateLimiter
from pest_bot.reddit_client import RedditError
from tests.conftest import make_post


class FakeReddit:
    username = "pestbot"

    def __init__(self, posts, *, replied=False, fail_reply=False):
        self.posts = posts
        self.replied = replied
        self.fail_reply = fail_reply
        self.replies = []

    def new_posts(self, subreddit, limit):
        return iter(self.posts)

    def refresh(self, post_id):
        return next(p for p in self.posts if p.id == post_id)

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
        self.calls = 0

    def draft(self, post):
        self.calls += 1
        if self.skip:
            return Draft(reply=None, skip_reason="not a question", model="m")
        return Draft(reply=self.reply, skip_reason=None, model="m")


def test_scan_drafts_pending_in_approval_mode(settings):
    settings.approval_mode = True
    reddit = FakeReddit([make_post(id="p1"), make_post(id="p2", is_self=False)])
    bot = Bot(settings, st.Store(), reddit, FakeAnswerer())
    r = bot.scan()
    assert (r.seen, r.drafted, r.skipped_filter, r.posted) == (2, 1, 1, 0)
    assert bot.store.get("p1").status == st.PENDING
    assert reddit.replies == []
    # Second scan does not re-process.
    r2 = bot.scan()
    assert r2.seen == 0


def test_scan_posts_immediately_when_not_in_approval_mode(settings):
    settings.approval_mode = False
    reddit = FakeReddit([make_post(id="p1")])
    bot = Bot(settings, st.Store(), reddit, FakeAnswerer())
    r = bot.scan()
    assert r.posted == 1
    post_id, body = reddit.replies[0]
    assert post_id == "p1"
    assert body.startswith("use bait not spray.")
    assert body.endswith("^(bot run by u/pestbot)")
    assert bot.store.get("p1").status == st.POSTED


def test_model_skip_is_recorded(settings):
    reddit = FakeReddit([make_post(id="p1")])
    bot = Bot(settings, st.Store(), reddit, FakeAnswerer(skip=True))
    r = bot.scan()
    assert r.skipped_model == 1
    assert bot.store.get("p1").reason == "model: not a question"


def test_answerer_failure_does_not_mark_seen(settings):
    class Boom:
        def draft(self, post):
            raise RuntimeError("api down")

    reddit = FakeReddit([make_post(id="p1")])
    bot = Bot(settings, st.Store(), reddit, Boom())
    r = bot.scan()
    assert r.errors and not bot.store.has_seen("p1")


def test_post_approved_respects_rate_limit(settings):
    settings.approval_mode = True
    settings.max_comments_per_hour = 1
    settings.min_seconds_between_comments = 0
    reddit = FakeReddit([make_post(id="p1"), make_post(id="p2")])
    store = st.Store()
    bot = Bot(settings, store, reddit, FakeAnswerer())
    bot.scan()
    for pid in ("p1", "p2"):
        store.set_status(pid, st.APPROVED)
    r = bot.post_approved()
    assert r.posted == 1 and r.held_rate_limit == 1
    assert store.get("p2").status == st.APPROVED


def test_post_skips_closed_or_already_replied(settings):
    store = st.Store()
    reddit = FakeReddit([make_post(id="p1", locked=True), make_post(id="p2")], replied=True)
    bot = Bot(settings, store, reddit, FakeAnswerer())
    for pid in ("p1", "p2"):
        store.record_draft(pid, "pestcontrol", "t", "u", "d", status=st.APPROVED)
    settings.min_seconds_between_comments = 0
    r = bot.post_approved()
    assert r.skipped == 2 and reddit.replies == []
    assert "closed" in store.get("p1").reason
    assert "already replied" in store.get("p2").reason


def test_post_failure_marked_failed(settings):
    store = st.Store()
    reddit = FakeReddit([make_post(id="p1")], fail_reply=True)
    bot = Bot(settings, store, reddit, FakeAnswerer())
    store.record_draft("p1", "pestcontrol", "t", "u", "d", status=st.APPROVED)
    r = bot.post_approved()
    assert r.failed == 1 and store.get("p1").status == st.FAILED


def test_dry_run_never_posts(settings):
    store = st.Store()
    reddit = FakeReddit([make_post(id="p1")])
    bot = Bot(settings, store, reddit, FakeAnswerer(), dry_run=True)
    store.record_draft("p1", "pestcontrol", "t", "u", "d", status=st.APPROVED)
    r = bot.post_approved()
    assert r.posted == 1 and reddit.replies == []
    assert store.get("p1").status == st.APPROVED


def test_rate_limiter_spacing(settings):
    store = st.Store()
    settings.min_seconds_between_comments = 600
    settings.max_comments_per_hour = 5
    limiter = RateLimiter(settings, store)
    now = time.time()
    assert limiter.wait_seconds(now) == 0
    store.record_draft("p1", "x", "t", "u", "d", status=st.APPROVED)
    store.record_posted("p1", "c1", posted_at=now - 100)
    assert 499 < limiter.wait_seconds(now) <= 500


def test_no_footer_when_blank(settings):
    settings.disclosure_footer = ""
    bot = Bot(settings, st.Store(), FakeReddit([]), FakeAnswerer())
    assert bot.render_comment("  hi  ") == "hi"
