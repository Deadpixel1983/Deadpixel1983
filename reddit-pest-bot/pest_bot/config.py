"""Load bot settings from config.yaml and credentials from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when configuration or credentials are missing or invalid."""


@dataclass
class Credentials:
    reddit_client_id: str
    reddit_client_secret: str
    reddit_username: str
    reddit_password: str
    anthropic_api_key: str | None


@dataclass
class Settings:
    approval_mode: bool = True
    subreddits: list[str] = field(default_factory=list)
    posts_per_subreddit: int = 25
    max_post_age_hours: float = 24
    skip_if_comments_at_least: int = 8
    self_posts_only: bool = True
    min_body_chars: int = 40
    keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    max_comments_per_hour: int = 3
    min_seconds_between_comments: int = 600
    disclosure_footer: str = ""
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_tokens: int = 2048
    database_path: str = "pest_bot.db"

    @classmethod
    def from_dict(cls, raw: dict) -> "Settings":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known
        if unknown:
            raise ConfigError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        settings = cls(**raw)
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.subreddits:
            raise ConfigError("config: 'subreddits' must list at least one subreddit")
        if not self.keywords:
            raise ConfigError("config: 'keywords' must list at least one keyword")
        if self.max_comments_per_hour < 1:
            raise ConfigError("config: 'max_comments_per_hour' must be at least 1")
        if self.min_seconds_between_comments < 0:
            raise ConfigError("config: 'min_seconds_between_comments' must be >= 0")
        if self.effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ConfigError("config: 'effort' must be one of low, medium, high, xhigh, max")
        if self.max_tokens < 256:
            raise ConfigError("config: 'max_tokens' must be at least 256")


def load_settings(path: str | Path = "config.yaml") -> Settings:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Config file {path} must contain a mapping at the top level")
    return Settings.from_dict(raw)


def load_credentials(env_path: str | Path | None = ".env", *, require_anthropic: bool = True) -> Credentials:
    """Read credentials from the environment, loading a .env file first if present."""
    if env_path is not None and Path(env_path).exists():
        load_dotenv(env_path)

    def need(name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise ConfigError(f"Missing credential: set {name} in .env or the environment")
        return value

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip() or None
    if require_anthropic and not anthropic_key:
        raise ConfigError("Missing credential: set ANTHROPIC_API_KEY in .env or the environment")

    return Credentials(
        reddit_client_id=need("REDDIT_CLIENT_ID"),
        reddit_client_secret=need("REDDIT_CLIENT_SECRET"),
        reddit_username=need("REDDIT_USERNAME"),
        reddit_password=need("REDDIT_PASSWORD"),
        anthropic_api_key=anthropic_key,
    )
