import ssl

import certifi

from lib.tls import ssl_context


def _empty_store_factory(monkeypatch):
    real = ssl.create_default_context
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs.get("cafile"))
        if kwargs.get("cafile"):
            return real(*args, **kwargs)
        return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # empty trust store

    monkeypatch.setattr(ssl, "create_default_context", fake)
    return calls


def test_empty_default_store_falls_back_to_certifi(monkeypatch):
    calls = _empty_store_factory(monkeypatch)
    ctx = ssl_context({})
    assert certifi.where() in calls
    assert ctx.get_ca_certs()
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_ca_bundle_from_env_is_used_as_cafile(monkeypatch):
    calls = _empty_store_factory(monkeypatch)
    ssl_context({"SB_CA_BUNDLE": certifi.where()})
    assert calls == [certifi.where()]


def test_insecure_flag_disables_verification():
    for value in ("1", "true", "YES"):
        ctx = ssl_context({"SB_INSECURE_TLS": value})
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False


def test_os_environ_is_ignored(monkeypatch):
    monkeypatch.setenv("SB_INSECURE_TLS", "1")
    monkeypatch.setenv("SB_CA_BUNDLE", "/nonexistent")
    ctx = ssl_context({})
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True
