import pytest

from helper_bot import store as st


def test_skip_and_seen():
    s = st.Store()
    assert not s.has_seen("a")
    s.record_skip("a", "pestcontrol", "t", "u", "no keyword")
    assert s.has_seen("a")
    rec = s.get("a")
    assert rec.status == st.SKIPPED and rec.niche == "" and rec.account == "default"
    assert s.counts() == {st.SKIPPED: 1}


def test_draft_lifecycle():
    s = st.Store()
    s.record_draft("b", "pestcontrol", "t", "u", "draft text", niche="pest_control", account="pest")
    pending = s.list_by_status(st.PENDING)
    assert pending[0].draft == "draft text" and pending[0].account == "pest"
    assert s.list_by_status(st.PENDING, niche="other") == []
    s.set_status("b", st.APPROVED, draft="edited")
    rec = s.get("b")
    assert rec.status == st.APPROVED and rec.draft == "edited"
    s.record_posted("b", "c1", posted_at=1000.0)
    rec = s.get("b")
    assert rec.status == st.POSTED and rec.comment_id == "c1"
    assert s.comments_since(999.0, account="pest") == 1
    assert s.comments_since(999.0, account="food") == 0
    assert s.comments_since(1001.0, account="pest") == 0
    assert s.last_comment_time(account="pest") == 1000.0
    assert s.last_comment_time(account="food") is None
    assert s.counts_by_niche() == {"pest_control": {st.POSTED: 1}}
    assert s.counts(niche="pest_control") == {st.POSTED: 1}


def test_set_status_unknown_post_raises():
    s = st.Store()
    with pytest.raises(KeyError):
        s.set_status("nope", st.APPROVED)
    with pytest.raises(KeyError):
        s.record_posted("nope", "c")


def test_bad_status_rejected():
    s = st.Store()
    s.record_draft("c", "x", "t", "u", "d")
    with pytest.raises(ValueError):
        s.set_status("c", "bogus")
    with pytest.raises(ValueError):
        s.record_draft("c", "x", "t", "u", "d", status=st.POSTED)


def test_persists_to_file(tmp_path):
    path = tmp_path / "t.db"
    with st.Store(path) as s:
        s.record_skip("a", "x", "t", "u", "r")
    with st.Store(path) as s:
        assert s.has_seen("a")
