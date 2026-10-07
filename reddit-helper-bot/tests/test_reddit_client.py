from types import SimpleNamespace

import pytest
import requests

from helper_bot import reddit_client
from helper_bot.config import ConfigError, Credentials, validate_proxy_url
from helper_bot.reddit_client import RedditClient, RedditError, build_session, mask_proxy


def creds(**overrides):
    base = dict(account="pest", reddit_client_id="id", reddit_client_secret="sec", reddit_username="pestbot", reddit_password="pw", anthropic_api_key=None, proxy=None)
    base.update(overrides)
    return Credentials(**base)


def test_build_session_without_proxy():
    s = build_session(None)
    assert s.proxies == {} and s.trust_env is True


def test_build_session_with_proxy_pins_both_schemes_and_ignores_env():
    s = build_session("http://u:p@proxy.example:8080")
    assert s.proxies == {"http": "http://u:p@proxy.example:8080", "https": "http://u:p@proxy.example:8080"}
    assert s.trust_env is False


def test_mask_proxy():
    assert mask_proxy(None) == "none"
    assert mask_proxy("http://user:secret@proxy.example:8080") == "http://***@proxy.example:8080"
    assert mask_proxy("socks5h://proxy.example:1080") == "socks5h://proxy.example:1080"


def test_validate_proxy_url():
    assert validate_proxy_url(" socks5h://u:p@h:1080 ", account="a") == "socks5h://u:p@h:1080"
    for bad in ("proxy.example:8080", "ftp://h:1", "http://", "just words"):
        with pytest.raises(ConfigError):
            validate_proxy_url(bad, account="a")


def test_client_passes_its_session_to_praw(monkeypatch):
    captured = {}

    def fake_reddit(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(reddit_client.praw, "Reddit", fake_reddit)
    c = RedditClient(creds(proxy="http://u:p@proxy.example:8080"))
    assert captured["requestor_kwargs"]["session"] is c.session
    assert c.session.proxies["https"] == "http://u:p@proxy.example:8080"
    assert captured["username"] == "pestbot" and "pestbot" in captured["user_agent"]
    assert c.account == "pest" and c.proxy == "http://u:p@proxy.example:8080"


def test_proxy_loaded_from_env(monkeypatch):
    from helper_bot.config import load_credentials

    for k in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        monkeypatch.setenv(f"REDDIT_PEST_{k}", "p")
    monkeypatch.setenv("REDDIT_PEST_PROXY", "http://u:p@proxy.example:8080")
    assert load_credentials(None, account="pest", require_anthropic=False).proxy == "http://u:p@proxy.example:8080"
    monkeypatch.setenv("REDDIT_PEST_PROXY", "nonsense")
    with pytest.raises(ConfigError):
        load_credentials(None, account="pest", require_anthropic=False)
    monkeypatch.setenv("REDDIT_PEST_PROXY", "")
    assert load_credentials(None, account="pest", require_anthropic=False).proxy is None


def test_egress_ip_uses_the_account_session():
    c = RedditClient(creds(), reddit=SimpleNamespace())
    calls = []

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ip": "203.0.113.7"}

    def fake_get(url, timeout, headers):
        calls.append(url)
        return FakeResp()

    c.session.get = fake_get
    assert c.egress_ip() == "203.0.113.7"
    assert calls == [reddit_client.IP_ECHO_URL]

    def failing_get(url, timeout, headers):
        raise requests.ConnectionError("proxy down")

    c.session.get = failing_get
    with pytest.raises(RedditError, match="proxy down"):
        c.egress_ip()
