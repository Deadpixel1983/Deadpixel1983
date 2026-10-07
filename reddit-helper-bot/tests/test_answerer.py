from types import SimpleNamespace

from helper_bot.answerer import SKIP_PREFIX, VOICE_RULES, Answerer, build_system_prompt
from tests.conftest import FOOD_PERSONA, PEST_PERSONA, make_post


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text, stop = self.replies.pop(0)
        content = [SimpleNamespace(type="text", text=text)] if text is not None else []
        return SimpleNamespace(
            content=content,
            stop_reason=stop,
            stop_details=SimpleNamespace(category="general_harms") if stop == "refusal" else None,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        )


def fake_client(*replies):
    messages = FakeMessages(replies)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def test_good_reply_returned_as_is(settings, niche):
    client, msgs = fake_client(("probably odorous house ants. liquid bait on the trail, don't spray.", "end_turn"))
    d = Answerer(settings, client=client).draft(make_post(), niche)
    assert not d.skipped
    assert d.reply.startswith("probably odorous")
    assert d.input_tokens == 100
    call = msgs.calls[0]
    assert call["model"] == settings.model
    assert call["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in call["betas"]
    assert call["output_config"] == {"effort": settings.effort}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["system"][0]["text"].startswith(PEST_PERSONA)
    assert VOICE_RULES in call["system"][0]["text"]


def test_each_niche_gets_its_own_persona(settings, niche, food_niche):
    client, msgs = fake_client(("fine.", "end_turn"), ("fine.", "end_turn"))
    a = Answerer(settings, client=client)
    a.draft(make_post(), niche)
    a.draft(make_post(), food_niche)
    assert PEST_PERSONA in msgs.calls[0]["system"][0]["text"]
    assert FOOD_PERSONA in msgs.calls[1]["system"][0]["text"]
    assert FOOD_PERSONA not in msgs.calls[0]["system"][0]["text"]
    # Prompt is stable across calls for the same niche (cache friendly).
    assert a.system_prompt(niche) is a.system_prompt(niche)


def test_skip_line_is_honoured(settings, niche):
    client, _ = fake_client((f"{SKIP_PREFIX} not a question", "end_turn"))
    d = Answerer(settings, client=client).draft(make_post(), niche)
    assert d.skipped and d.skip_reason == "not a question"


def test_ai_sounding_reply_is_rewritten_once(settings, niche):
    client, msgs = fake_client(
        ("Great question! Here's a breakdown:\n- a\n- b\n- c\n- d\nI hope this helps!", "end_turn"),
        ("liquid bait on the trail, leave it alone for a few days.", "end_turn"),
    )
    d = Answerer(settings, client=client).draft(make_post(), niche)
    assert not d.skipped
    assert d.reply == "liquid bait on the trail, leave it alone for a few days."
    assert len(msgs.calls) == 2
    assert msgs.calls[1]["messages"][1]["role"] == "assistant"
    assert "rejected" in msgs.calls[1]["messages"][2]["content"]
    assert d.input_tokens == 200


def test_still_ai_after_rewrite_is_skipped(settings, niche):
    client, _ = fake_client(
        ("Great question! bait.", "end_turn"),
        ("Certainly! Great question again.", "end_turn"),
    )
    d = Answerer(settings, client=client).draft(make_post(), niche)
    assert d.skipped and "still read as AI" in d.skip_reason


def test_refusal_becomes_skip(settings, niche):
    client, _ = fake_client((None, "refusal"))
    d = Answerer(settings, client=client).draft(make_post(), niche)
    assert d.skipped and "declined" in d.skip_reason


def test_markdown_is_scrubbed(settings, niche):
    client, _ = fake_client(("**gel bait** — not spray. that's it.", "end_turn"))
    d = Answerer(settings, client=client).draft(make_post(), niche)
    assert d.reply == "gel bait, not spray. that's it."


def test_samples_go_into_system_prompt(settings, niche, food_niche):
    samples = {"pest_control": [{"title": "ants?", "reply": "bait them, don't spray"}, {"title": "", "reply": ""}]}
    prompt = build_system_prompt(niche, samples["pest_control"])
    assert "bait them, don't spray" in prompt
    assert "Example 1." in prompt and "Example 2." not in prompt
    assert build_system_prompt(niche, []) == build_system_prompt(niche, None)
    client, msgs = fake_client(("fine answer here.", "end_turn"), ("fine answer here.", "end_turn"))
    a = Answerer(settings, client=client, samples=samples)
    a.draft(make_post(), niche)
    a.draft(make_post(), food_niche)
    assert "bait them, don't spray" in msgs.calls[0]["system"][0]["text"]
    assert "bait them, don't spray" not in msgs.calls[1]["system"][0]["text"]
