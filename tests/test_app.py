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
    photo = {"id": "p1", "photographer": "Jane", "source_url": "u", "description": "d"}
    with patch("app.tvcontrol.recent_photos", return_value=[]), \
         patch("app.unsplash.fetch_art_image", return_value=(b"jpeg", photo)) as fetch, \
         patch("app.tvcontrol.apply_art", return_value={"uploaded_id": "N1", "port": 8002}), \
         patch("app.tvcontrol.record_photo") as record:
        resp = client().post("/tvs/1.2.3.4/art-mode", json={"keywords": "Dubai"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["photo"]["id"] == "p1"
    fetch.assert_called_once_with("envkey", "Dubai", exclude_ids=[])
    record.assert_called_once_with("1.2.3.4", "p1")


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
    photo = {"id": "p1", "photographer": "Jane", "source_url": "u", "description": "d"}
    with patch("app.tvcontrol.recent_photos", return_value=["prev"]), \
         patch("app.unsplash.fetch_art_image", return_value=(b"jpeg", photo)) as fetch, \
         patch("app.tvcontrol.apply_art", return_value={"uploaded_id": "N1", "port": 8002}) as apply, \
         patch("app.tvcontrol.record_photo"):
        client().post("/tvs/1.2.3.4/art-mode", json={"keywords": "Dubai", "mac": "AA:BB:CC:DD:EE:FF"})
    assert apply.call_args.kwargs["mac"] == "AA:BB:CC:DD:EE:FF"
    assert fetch.call_args.kwargs["exclude_ids"] == ["prev"]


def test_art_mode_logs_request_and_done(monkeypatch, caplog):
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "envkey")
    photo = {"id": "p1", "photographer": "Jane", "source_url": "u", "description": "d"}
    with caplog.at_level("INFO"), \
         patch("app.tvcontrol.recent_photos", return_value=[]), \
         patch("app.unsplash.fetch_art_image", return_value=(b"jpeg", photo)), \
         patch("app.tvcontrol.apply_art", return_value={"uploaded_id": "N1", "port": 8002}), \
         patch("app.tvcontrol.record_photo"):
        client().post("/tvs/1.2.3.4/art-mode", json={"keywords": "Kyoto"})
    text = " ".join(r.message for r in caplog.records)
    assert "art-mode tv=1.2.3.4" in text
    assert "keywords='Kyoto'" in text
    assert "done success in" in text
    # every art-mode record shares one non-default request id
    ids = {getattr(r, "request_id", "-") for r in caplog.records if "art-mode" in r.message or "done" in r.message}
    assert ids and ids != {"-"}


def test_sequential_requests_get_distinct_ids(monkeypatch, caplog):
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "envkey")
    photo = {"id": "p1", "photographer": "Jane", "source_url": "u", "description": "d"}
    ids = []
    with patch("app.tvcontrol.recent_photos", return_value=[]), \
         patch("app.unsplash.fetch_art_image", return_value=(b"jpeg", photo)), \
         patch("app.tvcontrol.apply_art", return_value={"uploaded_id": "N1", "port": 8002}), \
         patch("app.tvcontrol.record_photo"):
        for _ in range(2):
            caplog.clear()
            with caplog.at_level("INFO"):
                client().post("/tvs/1.2.3.4/art-mode", json={"keywords": "Kyoto"})
            ids.append(next(r.request_id for r in caplog.records if "art-mode tv=" in r.message))
    assert ids[0] != ids[1] and "-" not in ids
