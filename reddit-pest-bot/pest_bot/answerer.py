"""Turn a Reddit post into a draft reply using Claude."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import anthropic

from . import style
from .config import Settings
from .models import Post

log = logging.getLogger(__name__)

SKIP_PREFIX = "SKIP:"

SYSTEM_PROMPT = """You are a regular on pest-control subreddits who happens to know a lot about bugs, rodents, and getting rid of them. You have done it yourself for years. You answer people's questions the way a real person does in a Reddit comment.

How you write:
Sound like a human typing a reply on their phone, not an article. Casual, direct, a little blunt is fine. Contractions, lowercase-ish sentence starts are fine, light slang is fine. Answer the one thing they actually asked and stop. Do not explain the whole topic. Do not give background they didn't ask for. If they asked what the bug is, name it and say the one thing that matters about it. If they asked how to get rid of something, say what you'd actually do, in the order you'd do it, in a couple of sentences.

Length: usually 2 to 6 sentences. One or two short paragraphs. Never more than about 900 characters. Shorter is better.

Never use: headers, bullet points, numbered lists, bold text, em dashes, "Great question", "Hope this helps", "I'd recommend consulting", "It's important to note", "Additionally", "Furthermore", "In summary", or any sign-off. Don't restate their question back to them. Don't open with a greeting. Don't say you're an AI, and don't say you're not an AI. Don't apologize.

Substance:
Be practically correct. Prefer the cheap, boring fix that actually works (sealing entry points, sanitation, specific bait types, moisture control, a proper bed bug protocol, etc) over generic advice. Name specific product categories or active ingredients when it genuinely helps (for example gel bait for roaches, fipronil-based ant bait, a bait station for mice) but never give pesticide dosages or mixing instructions. If the right answer is "call a pro", say that plainly and say why in one line (termites, a big wasp nest in a wall, anything venomous, a heavy infestation). If kids, pets, pregnancy, or health symptoms are involved, steer toward the safe option and say so briefly. If you're not sure what the bug is from a text description, say what it probably is and what would narrow it down.

Never recommend anything illegal, like misusing a restricted pesticide, using a product indoors that is outdoor-only, or poisoning an animal in a way that's prohibited. Never give medical advice beyond "see a doctor if X".

Do not include any links, product brand names used as advertising, or mention of any company or service.

When to skip:
If the post isn't really asking a pest question, is a rant or a story with no question, is asking for a product recommendation by brand, is about something you can't answer without seeing a photo, is a joke, or is in a language other than English, reply with exactly one line starting with "SKIP:" followed by a short reason. Otherwise reply with the comment text only, nothing else."""

EXAMPLES_INTRO = """

Here are real replies from these subreddits that people upvoted. This is the register to write in: notice how short they are, how they skip preamble, and how they just say the thing. Match the tone, not the content.
"""

MAX_EXAMPLES = 12

REWRITE_NUDGE = """Your previous draft got rejected for reading like an AI wrote it. Problems: {problems}

Rewrite it. Make it shorter and more casual, like a real person who replied in 30 seconds. Plain sentences, no formatting, no lists, no sign-off, answer only what they asked."""


@dataclass
class Draft:
    """Result of asking Claude about a post."""

    reply: str | None          # the comment to post, or None if skipped
    skip_reason: str | None    # why we skipped, if we did
    model: str
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def skipped(self) -> bool:
        return self.reply is None


def build_system_prompt(samples: list[dict] | None) -> str:
    """Rules plus up to MAX_EXAMPLES real replies as few-shot style examples."""
    if not samples:
        return SYSTEM_PROMPT
    parts = [SYSTEM_PROMPT, EXAMPLES_INTRO]
    for i, s in enumerate(samples[:MAX_EXAMPLES], start=1):
        title = str(s.get("title", "")).strip()
        reply = str(s.get("reply", "")).strip()
        if not reply:
            continue
        parts.append(f"\nExample {i}. Someone asked: {title}\nA human replied:\n{reply}\n")
    return "".join(parts)


class Answerer:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None, *, samples: list[dict] | None = None):
        self.settings = settings
        self.client = client or anthropic.Anthropic()
        # Built once so the cached system prefix is byte-identical across calls.
        self.system_prompt = build_system_prompt(samples)

    def draft(self, post: Post) -> Draft:
        """Ask Claude for a reply. Returns a skipped Draft if the post isn't answerable."""
        messages = [{"role": "user", "content": _render_post(post)}]
        text, usage = self._call(messages)

        if text.strip().upper().startswith(SKIP_PREFIX):
            reason = text.strip()[len(SKIP_PREFIX):].strip() or "model chose to skip"
            return Draft(reply=None, skip_reason=reason, model=self.settings.model, **usage)

        reply = style.scrub(text)
        issues = style.problems(reply)
        if issues:
            log.info("draft for %s needs rewrite: %s", post.id, "; ".join(issues))
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": REWRITE_NUDGE.format(problems="; ".join(issues))})
            text2, usage2 = self._call(messages)
            usage = {k: usage[k] + usage2[k] for k in usage}
            if text2.strip().upper().startswith(SKIP_PREFIX):
                reason = text2.strip()[len(SKIP_PREFIX):].strip() or "model chose to skip"
                return Draft(reply=None, skip_reason=reason, model=self.settings.model, **usage)
            reply = style.scrub(text2)
            issues = style.problems(reply)
            if issues:
                return Draft(
                    reply=None,
                    skip_reason="draft still read as AI after rewrite: " + "; ".join(issues),
                    model=self.settings.model,
                    **usage,
                )

        return Draft(reply=reply, skip_reason=None, model=self.settings.model, **usage)

    def _call(self, messages: list[dict]) -> tuple[str, dict[str, int]]:
        """One Messages API call. Returns (text, usage). Raises on API failure."""
        response = self.client.beta.messages.create(
            model=self.settings.model,
            max_tokens=self.settings.max_tokens,
            system=[{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_config={"effort": self.settings.effort},
            # If a safety classifier declines, re-run on Anthropic's recommended fallback
            # model inside the same call instead of returning an empty refusal.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(response.usage, "output_tokens", 0) or 0,
        }

        if response.stop_reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            return f"{SKIP_PREFIX} model declined to answer (category: {category})", usage

        if response.stop_reason == "max_tokens":
            log.warning("response hit max_tokens; draft may be truncated")

        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            return f"{SKIP_PREFIX} model returned no text", usage
        return text, usage


def _render_post(post: Post) -> str:
    body = post.body.strip() or "(no body text)"
    return (
        f"Subreddit: r/{post.subreddit}\n"
        f"Title: {post.title.strip()}\n\n"
        f"{body}"
    )
