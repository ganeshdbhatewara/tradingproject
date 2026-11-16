import pytest
from papertrade.engine import Portfolio
from papertrade.persistence import load_state, save_state


def test_local_persistence_roundtrip_is_labeled_ephemeral(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    path = tmp_path / "state.json"
    p = Portfolio(cash_inr=12345)
    label = save_state(p, path)
    restored, load_label = load_state(path)
    assert restored.cash_inr == 12345
    assert label == load_label and "not durable" in label


def test_cloud_configuration_requires_both_values(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(RuntimeError):
        load_state(tmp_path / "state.json")


def test_cloud_state_roundtrip_uses_authenticated_rest_and_upsert(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-server-key")
    monkeypatch.setenv("PAPER_STATE_ID", "paper-test")
    stored = {}

    class Response:
        def __init__(self, payload=None): self.payload = payload
        def raise_for_status(self): pass
        def json(self): return self.payload

    def post(url, **kwargs):
        stored.update(kwargs["json"])
        stored["revision"] = kwargs["json"]["revision"]
        assert kwargs["params"] == {"on_conflict": "state_key"}
        assert kwargs["headers"]["Authorization"] == "Bearer test-server-key"
        return Response([{"revision": stored["revision"]}])

    def patch(url, **kwargs):
        assert kwargs["params"]["revision"] == "eq.1"
        stored.update(kwargs["json"])
        return Response([{"revision": stored["revision"]}])

    def get(url, **kwargs):
        return Response([stored] if stored else [])

    monkeypatch.setattr("papertrade.persistence.requests.post", post)
    monkeypatch.setattr("papertrade.persistence.requests.patch", patch)
    monkeypatch.setattr("papertrade.persistence.requests.get", get)
    p = Portfolio(cash_inr=23456)
    assert "Supabase" in save_state(p, tmp_path / "ignored.json")
    restored, label = load_state(tmp_path / "ignored.json")
    assert restored.cash_inr == 23456 and "Supabase" in label
    restored.cash_inr -= 1
    save_state(restored, tmp_path / "ignored.json")
    assert stored["revision"] == 2
