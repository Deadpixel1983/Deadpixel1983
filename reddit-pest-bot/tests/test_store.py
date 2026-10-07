import pytest

from pest_bot import store as st


def test_skip_and_seen():
    s = st.Store()
    assert not s.has_seen("a")
    s.record_skip("a", "pestcontrol", "t", "u", "no keyword")
    assert s.has_seen("a")
    assert s.get("a").status == st.SKIPPED
    assert s.counts() == {st.SKIPPED: 1}


def test_draft_lifecycle():
    s = st.Store()
    s.record_draft("b", "pestcontrol", "t", "u", "draft text")
    assert s.list_by_status(st.PENDING)[0].draft == "draft text"
    s.set_status("b", st.APPROVED, draft="edited")
    rec = s.get("b")
    assert rec.status == st.APPROVED and rec.draft == "edited"
    s.record_posted("b", "c1", posted_at=1000.0)
    rec = s.get("b")
    assert rec.status == st.POSTED and rec.comment_id == "c1"
    assert s.comments_since(999.0) == 1
    assert s.comments_since(1001.0) == 0
    assert s.last_comment_time() == 1000.0


def test_set_status_unknown_post_raises():
    s = st.Store()
    with pytest.raises(KeyError):
        s.set_status("nope", st.APPROVED)


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
