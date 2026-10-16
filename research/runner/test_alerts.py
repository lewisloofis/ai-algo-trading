def test_default_post_path_passes_ssl_context(monkeypatch, tmp_path):
    import ssl

    from runner import alerts

    seen = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        seen["context"] = context
        return _Resp()

    monkeypatch.setattr(alerts.urllib.request, "urlopen", fake_urlopen)
    env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}
    a = alerts.Alerter(tmp_path / "a.log", env)
    assert "telegram" in a.send(alerts.ALERT, "x")
    assert isinstance(seen["context"], ssl.SSLContext)
