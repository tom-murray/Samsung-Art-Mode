from unittest.mock import patch
import app as flask_app


def client():
    flask_app.app.config["TESTING"] = True
    return flask_app.app.test_client()


def test_health_ok():
    resp = client().get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_art_mode_missing_keywords_returns_400():
    resp = client().post("/tvs/1.2.3.4/art-mode", json={"access_key": "k"})
    assert resp.status_code == 400


def test_art_mode_missing_access_key_returns_400(monkeypatch):
    monkeypatch.delenv("UNSPLASH_ACCESS_KEY", raising=False)
    resp = client().post("/tvs/1.2.3.4/art-mode", json={"keywords": "Dubai"})
    assert resp.status_code == 400


def test_art_mode_happy_path(monkeypatch):
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "envkey")
    with patch("app.unsplash.fetch_art_image", return_value=b"jpeg") as fetch, \
         patch("app.tvcontrol.apply_art", return_value={"uploaded_id": "N1", "port": 8002}) as apply:
        resp = client().post("/tvs/1.2.3.4/art-mode", json={"keywords": "Dubai"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "success"
    fetch.assert_called_once_with("envkey", "Dubai")
    apply.assert_called_once()


def test_pair_returns_504_when_not_paired():
    with patch("app.tvcontrol.pair", return_value={"paired": False, "port": 8002, "token_file": "/t", "error": "timeout"}):
        resp = client().post("/tvs/1.2.3.4/pair")
    assert resp.status_code == 504


def test_diagnose_returns_report():
    with patch("app.tvcontrol.diagnose", return_value={"ip": "1.2.3.4", "rest": {}, "ports": {}}):
        resp = client().get("/tvs/1.2.3.4/diagnose")
    assert resp.status_code == 200
    assert resp.get_json()["ip"] == "1.2.3.4"


def test_state_returns_report():
    with patch("app.tvcontrol.state", return_value={"reachable": True, "power": "standby", "art_mode": "unknown", "summary": "standby"}):
        resp = client().get("/tvs/1.2.3.4/state")
    assert resp.status_code == 200
    assert resp.get_json()["power"] == "standby"


def test_wake_returns_result():
    with patch("app.tvcontrol.ensure_awake", return_value={"woken": True, "mac": "AA:BB:CC:DD:EE:FF"}) as wake:
        resp = client().post("/tvs/1.2.3.4/wake", json={"mac": "AA:BB:CC:DD:EE:FF"})
    assert resp.status_code == 200
    assert resp.get_json()["woken"] is True
    wake.assert_called_once_with("1.2.3.4", mac="AA:BB:CC:DD:EE:FF")


def test_art_mode_passes_mac_through(monkeypatch):
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "envkey")
    with patch("app.unsplash.fetch_art_image", return_value=b"jpeg"), \
         patch("app.tvcontrol.apply_art", return_value={"uploaded_id": "N1", "port": 8002}) as apply:
        client().post("/tvs/1.2.3.4/art-mode", json={"keywords": "Dubai", "mac": "AA:BB:CC:DD:EE:FF"})
    assert apply.call_args.kwargs["mac"] == "AA:BB:CC:DD:EE:FF"
