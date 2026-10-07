"""Load bot settings from config.yaml and credentials from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}


class ConfigError(Exception):
    """Raised when configuration or credentials are missing or invalid."""


DEFAULT_ACCOUNT = "default"


@dataclass
class Credentials:
    account: str
    reddit_client_id: str
    reddit_client_secret: str
    reddit_username: str
    reddit_password: str
    anthropic_api_key: str | None


@dataclass
class Niche:
    """One topic the bot helps with: where to look, what to look for, who it is."""

    name: str
    subreddits: list[str]
    keywords: list[str]
    persona: str
    exclude_keywords: list[str] = field(default_factory=list)
    account: str = DEFAULT_ACCOUNT        # which Reddit account answers; see env_prefix()
    samples_path: str | None = None       # defaults to samples/<name>.yaml
    disclosure_footer: str | None = None  # overrides the global footer when set
    enabled: bool = True

    _FIELDS = {"subreddits", "keywords", "persona", "exclude_keywords", "account", "samples_path", "disclosure_footer", "enabled"}

    @classmethod
    def from_dict(cls, name: str, raw: dict) -> "Niche":
        if not isinstance(raw, dict):
            raise ConfigError(f"config: niche '{name}' must be a mapping")
        unknown = set(raw) - cls._FIELDS
        if unknown:
            raise ConfigError(f"config: niche '{name}' has unknown keys: {', '.join(sorted(unknown))}")
        if not name.replace("_", "").isalnum():
            raise ConfigError(f"config: niche name '{name}' may only contain letters, digits and underscores")
        niche = cls(name=name, **raw)
        if not niche.subreddits:
            raise ConfigError(f"config: niche '{name}' must list at least one subreddit")
        if not niche.keywords:
            raise ConfigError(f"config: niche '{name}' must list at least one keyword")
        if not (niche.persona or "").strip():
            raise ConfigError(f"config: niche '{name}' needs a 'persona' describing who the bot is for this topic")
        if not niche.account or not niche.account.replace("_", "").isalnum():
            raise ConfigError(f"config: niche '{name}' account name may only contain letters, digits and underscores")
        if niche.samples_path is None:
            niche.samples_path = f"samples/{name}.yaml"
        return niche


@dataclass
class Settings:
    approval_mode: bool = True
    niches: dict[str, Niche] = field(default_factory=dict)
    posts_per_subreddit: int = 25
    max_post_age_hours: float = 24
    skip_if_comments_at_least: int = 8
    self_posts_only: bool = True
    min_body_chars: int = 40
    max_comments_per_hour: int = 3
    min_seconds_between_comments: int = 600
    disclosure_footer: str = ""
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    max_tokens: int = 2048
    database_path: str = "helper_bot.db"

    @classmethod
    def from_dict(cls, raw: dict) -> "Settings":
        raw = dict(raw)
        known = set(cls.__dataclass_fields__)
        unknown = set(raw) - known
        if unknown:
            raise ConfigError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        niches_raw = raw.pop("niches", None) or {}
        if not isinstance(niches_raw, dict):
            raise ConfigError("config: 'niches' must be a mapping of niche name to settings")
        settings = cls(**raw)
        settings.niches = {name: Niche.from_dict(name, n) for name, n in niches_raw.items()}
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.niches:
            raise ConfigError("config: 'niches' must define at least one niche")
        if not self.active_niches():
            raise ConfigError("config: every niche is disabled")
        if self.max_comments_per_hour < 1:
            raise ConfigError("config: 'max_comments_per_hour' must be at least 1")
        if self.min_seconds_between_comments < 0:
            raise ConfigError("config: 'min_seconds_between_comments' must be >= 0")
        if self.effort not in EFFORT_LEVELS:
            raise ConfigError("config: 'effort' must be one of low, medium, high, xhigh, max")
        if self.max_tokens < 256:
            raise ConfigError("config: 'max_tokens' must be at least 256")

    def active_niches(self, only: list[str] | None = None) -> list[Niche]:
        """Enabled niches, optionally restricted to the given names."""
        if only:
            missing = [n for n in only if n not in self.niches]
            if missing:
                raise ConfigError(f"unknown niche(s): {', '.join(missing)}. Known: {', '.join(self.niches)}")
            return [self.niches[n] for n in only if self.niches[n].enabled]
        return [n for n in self.niches.values() if n.enabled]

    def footer_for(self, niche: Niche) -> str:
        return niche.disclosure_footer if niche.disclosure_footer is not None else self.disclosure_footer

    def accounts(self, niches: list[Niche] | None = None) -> list[str]:
        """Distinct account names used by the given (default: all active) niches, in order."""
        return list(dict.fromkeys(n.account for n in (niches or self.active_niches())))


def load_settings(path: str | Path = "config.yaml") -> Settings:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Config file {path} must contain a mapping at the top level")
    return Settings.from_dict(raw)


def env_prefix(account: str) -> str:
    """The default account reads REDDIT_*; account 'foo' reads REDDIT_FOO_*."""
    return "REDDIT" if account == DEFAULT_ACCOUNT else f"REDDIT_{account.upper()}"


def load_credentials(env_path: str | Path | None = ".env", *, account: str = DEFAULT_ACCOUNT, require_anthropic: bool = True) -> Credentials:
    """Read one account's credentials from the environment, loading .env first if present."""
    if env_path is not None and Path(env_path).exists():
        load_dotenv(env_path)

    prefix = env_prefix(account)

    def need(suffix: str) -> str:
        name = f"{prefix}_{suffix}"
        value = os.environ.get(name, "").strip()
        if not value:
            raise ConfigError(f"Missing credential for account '{account}': set {name} in .env or the environment")
        return value

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip() or None
    if require_anthropic and not anthropic_key:
        raise ConfigError("Missing credential: set ANTHROPIC_API_KEY in .env or the environment")

    return Credentials(
        account=account,
        reddit_client_id=need("CLIENT_ID"),
        reddit_client_secret=need("CLIENT_SECRET"),
        reddit_username=need("USERNAME"),
        reddit_password=need("PASSWORD"),
        anthropic_api_key=anthropic_key,
    )


def load_all_credentials(env_path: str | Path | None, accounts: list[str], *, require_anthropic: bool = True) -> dict[str, Credentials]:
    """Credentials for every named account, keyed by account name."""
    return {a: load_credentials(env_path, account=a, require_anthropic=require_anthropic) for a in accounts}
