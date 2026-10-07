import pytest

from pest_bot.config import ConfigError, Settings, load_credentials, load_settings


def test_unknown_key_rejected():
    with pytest.raises(ConfigError):
        Settings.from_dict({"subreddits": ["a"], "keywords": ["b"], "bogus": 1})


def test_validation():
    with pytest.raises(ConfigError):
        Settings.from_dict({"subreddits": [], "keywords": ["b"]})
    with pytest.raises(ConfigError):
        Settings.from_dict({"subreddits": ["a"], "keywords": ["b"], "effort": "turbo"})


def test_shipped_config_loads():
    s = load_settings("config.yaml")
    assert s.approval_mode is True
    assert "pestcontrol" in s.subreddits
    assert "{username}" in s.disclosure_footer


def test_credentials_from_env(monkeypatch):
    for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD"):
        monkeypatch.setenv(k, "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        load_credentials(None)
    creds = load_credentials(None, require_anthropic=False)
    assert creds.reddit_username == "x" and creds.anthropic_api_key is None
