"""Keep replies sounding like a person on Reddit, not an AI essay."""

from __future__ import annotations

import re

# Phrases that almost never appear in a real Reddit comment but show up
# constantly in AI output. A draft containing one is sent back for a rewrite.
AI_TELLS = [
    "great question",
    "i hope this helps",
    "hope this helps",
    "i'd be happy to",
    "i would be happy to",
    "certainly!",
    "as an ai",
    "as a language model",
    "in conclusion",
    "in summary",
    "to summarize",
    "it's important to note",
    "it is important to note",
    "it's worth noting",
    "please note that",
    "here's a breakdown",
    "here is a breakdown",
    "let's break this down",
    "comprehensive",
    "furthermore",
    "additionally,",
    "moreover",
    "feel free to",
    "don't hesitate to",
    "best of luck",
    "good luck with your",
    "i'm not a professional, but",
]

# Hard cap on reply length (characters, before the disclosure footer).
MAX_CHARS = 1100


def scrub(text: str) -> str:
    """Remove formatting that reads as machine-generated. Returns plain prose."""
    out = text.strip()
    # Markdown headers -> plain line
    out = re.sub(r"^\s{0,3}#{1,6}\s*", "", out, flags=re.MULTILINE)
    # Bold / italics markers
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", out)
    out = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", out)
    out = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"\1", out)
    # Em and en dashes -> comma or period style punctuation
    out = out.replace(" — ", ", ").replace("—", ", ").replace(" – ", ", ").replace("–", "-")
    # Horizontal rules
    out = re.sub(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$", "", out, flags=re.MULTILINE)
    # Collapse 3+ blank lines
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def problems(text: str) -> list[str]:
    """Return a list of reasons the draft still reads as AI-written. Empty means OK."""
    issues: list[str] = []
    lowered = text.lower()

    for tell in AI_TELLS:
        if tell in lowered:
            issues.append(f"contains '{tell}'")

    if len(text) > MAX_CHARS:
        issues.append(f"too long ({len(text)} chars, max {MAX_CHARS})")

    bullet_lines = [ln for ln in text.splitlines() if re.match(r"^\s*([-*•]|\d+[.)])\s+", ln)]
    if len(bullet_lines) > 3:
        issues.append(f"{len(bullet_lines)} bullet/numbered lines; reads like a listicle")

    if re.search(r"^\s*#{1,6}\s", text, flags=re.MULTILINE):
        issues.append("contains markdown headers")

    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if len(paragraphs) > 4:
        issues.append(f"{len(paragraphs)} paragraphs; too much structure for a comment")

    return issues
