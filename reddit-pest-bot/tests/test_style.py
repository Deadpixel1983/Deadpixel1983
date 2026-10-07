from pest_bot import style


def test_scrub_strips_markdown():
    text = "## Quick answer\n\n**Gel bait** works — spray doesn't.\n\n---\n"
    out = style.scrub(text)
    assert out == "Quick answer\n\nGel bait works, spray doesn't."


def test_problems_flags_ai_tells():
    text = "Great question! Gel bait works. I hope this helps."
    issues = style.problems(text)
    assert any("great question" in i for i in issues)
    assert any("hope this helps" in i for i in issues)


def test_problems_flags_listicles():
    text = "Here's what to do:\n- one\n- two\n- three\n- four\n- five"
    issues = style.problems(text)
    assert any("bullet" in i for i in issues)


def test_problems_flags_length():
    issues = style.problems("x" * (style.MAX_CHARS + 1))
    assert any("too long" in i for i in issues)


def test_clean_reply_has_no_problems():
    text = "those are odorous house ants. put a liquid ant bait on the trail and leave it, don't spray. gone in a couple days."
    assert style.problems(text) == []
