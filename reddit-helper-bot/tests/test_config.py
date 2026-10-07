import pytest

from helper_bot.config import ConfigError, Settings, env_prefix, load_all_credentials, load_credentials, load_settings

GOOD_NICHE = {"subreddits": ["a"], "keywords": ["b"], "persona": "you know things"}


def test_unknown_key_rejected():
    with pytest.raises(ConfigError):
        Settings.from_dict({"niches": {"x": GOOD_NICHE}, "bogus": 1})
    with pytest.raises(ConfigError):
        Settings.from_dict({"niches": {"x": {**GOOD_NICHE, "bogus": 1}}})


def test_niche_validation():
    with pytest.raises(ConfigError):
        Settings.from_dict({"niches": {}})
    with pytest.raises(ConfigError):
        Settings.from_dict({"niches": {"x": {**GOOD_NICHE, "subreddits": []}}})
    with pytest.raises(ConfigError):
        Settings.from_dict({"niches": {"x": {**GOOD_NICHE, "persona": "  "}}})
    with pytest.raises(ConfigError):
        Settings.from_dict({"niches": {"bad name": GOOD_NICHE}})
    with pytest.raises(ConfigError):
        Settings.from_dict({"niches": {"x": {**GOOD_NICHE, "account": "bad-name"}}})
    with pytest.raises(ConfigError):
        Settings.from_dict({"niches": {"x": {**GOOD_NICHE, "enabled": False}}})
    with pytest.raises(ConfigError):
        Settings.from_dict({"niches": {"x": GOOD_NICHE}, "effort": "turbo"})


def test_defaults_and_helpers():
    s = Settings.from_dict({"niches": {"x": GOOD_NICHE, "y": {**GOOD_NICHE, "account": "acc", "enabled": False}}})
    assert s.niches["x"].account == "default"
    assert s.niches["x"].samples_path == "samples/x.yaml"
    assert [n.name for n in s.active_niches()] == ["x"]
    assert s.accounts() == ["default"]
    with pytest.raises(ConfigError):
        s.active_niches(["nope"])


def test_shipped_config_loads():
    s = load_settings("config.yaml")
    assert s.approval_mode is True
    assert set(s.niches) == {"pest_control", "property_selling", "food_hygiene", "holidays"}
    assert s.accounts() == ["pest", "property", "food", "travel"]
    assert "{username}" in s.disclosure_footer
    for n in s.niches.values():
        assert "Never" in n.persona  # every persona carries hard limits


def test_env_prefix():
    assert env_prefix("default") == "REDDIT"
    assert env_prefix("pest") == "REDDIT_PEST"


def test_credentials_from_env(monkeypatch):
    for k in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"REDDIT_{k}", "x")
        monkeypatch.setenv(f"REDDIT_PEST_{k}", "p")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        load_credentials(None)
    creds = load_credentials(None, require_anthropic=False)
    assert creds.reddit_username == "x" and creds.anthropic_api_key is None
    both = load_all_credentials(None, ["default", "pest"], require_anthropic=False)
    assert both["pest"].reddit_username == "p" and both["pest"].account == "pest"
    with pytest.raises(ConfigError, match="REDDIT_FOOD_CLIENT_ID"):
        load_credentials(None, account="food", require_anthropic=False)
