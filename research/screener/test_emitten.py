

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


def test_notation_reads_notation_code_and_stays_backward_compatible():
    from screener import emitten

    assert emitten._notation({"notation_code": "X", "notation_desc": "d", "icon_url": {}}) == "X"
    assert emitten._notation({"notation": "Y"}) == "Y"
    assert emitten._notation({"code": "Z"}) == "Z"
    assert emitten._notation("S") == "S"
    assert emitten._notation({"foo": 1}) == '{"foo": 1}'


def test_company_status_accepts_string_and_object():
    from screener import emitten

    assert emitten.company_status_text("STATUS_ACTIVE") == "STATUS_ACTIVE"
    assert emitten.company_status_text({"status": "SUSPENDED"}) == "SUSPENDED"
    assert emitten.company_status_text(None) == ""


def test_emitten_has_no_third_party_import_path():
    """build_ssl_context must not import screen_universe (pandas) - the box .pyz lacks it."""
    import inspect

    from screener import emitten

    assert "screener.screen_universe import" not in inspect.getsource(emitten.build_ssl_context)
