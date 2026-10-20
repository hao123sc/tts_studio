"""代理地址校验测试（FR-S-3：Edge-TTS 403/超时时的代理配置）。"""

from __future__ import annotations

from app.core.config import validate_proxy


def test_empty_is_direct():
    assert validate_proxy("") == ("", "")
    assert validate_proxy("   ") == ("", "")


def test_valid_http_and_https():
    assert validate_proxy("http://127.0.0.1:7890") == ("http://127.0.0.1:7890", "")
    assert validate_proxy("https://proxy.school.edu:8080") == ("https://proxy.school.edu:8080", "")
    assert validate_proxy("http://user:pass@10.0.0.1:3128") == ("http://user:pass@10.0.0.1:3128", "")


def test_missing_scheme_gets_http():
    assert validate_proxy("127.0.0.1:7890") == ("http://127.0.0.1:7890", "")


def test_invalid_schemes_rejected():
    for bad in ("socks5://127.0.0.1:1080", "ftp://x:1", "file:///tmp"):
        clean, err = validate_proxy(bad)
        assert clean == "" and "暂不支持" in err


def test_incomplete_address_rejected():
    for bad in ("http://", "http://host:port号", "http://host with space:1"):
        clean, err = validate_proxy(bad)
        assert clean == "" and err


def test_socks_hint_mentions_http():
    _, err = validate_proxy("socks5://127.0.0.1:1080")
    assert "http://" in err
