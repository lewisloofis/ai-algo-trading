

def test_build_ssl_context_falls_back_to_certifi_when_trust_store_empty(monkeypatch):
    """A bare macOS venv loads zero roots; the emitten client must use certifi
    like the chartbit path does, not fail with CERTIFICATE_VERIFY_FAILED."""
    import ssl

    import certifi

    from screener import emitten

    monkeypatch.delenv("SB_INSECURE_TLS", raising=False)
    monkeypatch.delenv("SB_CA_BUNDLE", raising=False)
    real = ssl.create_default_context
    calls = []

    def fake_create_default_context(*args, **kwargs):
        calls.append(kwargs.get("cafile"))
        if kwargs.get("cafile"):
            return real(*args, **kwargs)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # empty trust store
        return ctx

    monkeypatch.setattr(ssl, "create_default_context", fake_create_default_context)
    ctx = emitten.build_ssl_context()
    assert certifi.where() in calls
    assert ctx.get_ca_certs()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
