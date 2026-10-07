from pest_bot import study
from tests.conftest import make_post


def test_looks_like_question(settings):
    assert study.looks_like_question(make_post(), settings)
    assert not study.looks_like_question(make_post(title="Ant story", body="We had ants once. It was a time."), settings)
    assert not study.looks_like_question(make_post(is_self=False), settings)


def test_usable_reply_filters():
    good = {"author": "someone", "body": "bait them, do not spray. it takes a few days but it works and they stop coming back.", "score": 10, "is_op": False}
    assert study.usable_reply(good)
    assert not study.usable_reply({**good, "author": "AutoModerator"})
    assert not study.usable_reply({**good, "is_op": True})
    assert not study.usable_reply({**good, "score": 1})
    assert not study.usable_reply({**good, "body": "short"})
    assert not study.usable_reply({**good, "body": good["body"] + " see https://example.com"})


def test_collect_and_summarize(settings, tmp_path):
    class FakeClient:
        def top_posts(self, name, limit, time_filter):
            return iter([make_post(id="q1"), make_post(id="q2", title="no question here", body="Just a story about ants, nothing to ask.")])

        def top_level_comments(self, post_id, limit):
            return [
                {"id": "c1", "author": "a", "body": "bait, not spray. give it a week and keep the counters clean meanwhile.", "score": 20, "is_op": False},
                {"id": "c2", "author": "AutoModerator", "body": "x" * 100, "score": 50, "is_op": False},
                {"id": "c3", "author": "b", "body": "- one\n- two\n- three, and that is honestly all there is to it for ants in a kitchen.", "score": 5, "is_op": False},
            ]

    samples = study.collect(FakeClient(), settings)
    assert [s["score"] for s in samples] == [20, 5]
    stats = study.summarize(samples)
    assert stats["count"] == 2 and stats["percent_with_lists"] == 50
    path = tmp_path / "s.yaml"
    study.save(samples, path)
    assert study.load(path)[0]["reply"].startswith("bait, not spray")
    assert study.load(tmp_path / "missing.yaml") == []


def test_shipped_samples_load():
    assert len(study.load("samples.yaml")) >= 5
