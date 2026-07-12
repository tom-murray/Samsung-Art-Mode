import os
import time
from unittest.mock import MagicMock, patch

import tvcontrol


def test_call_bounded_returns_value_and_error():
    assert tvcontrol._call_bounded(lambda: 42) == (42, None)
    val, err = tvcontrol._call_bounded(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert val is None
    assert "boom" in err


def test_call_bounded_times_out_without_blocking():
    val, err = tvcontrol._call_bounded(lambda: time.sleep(1), timeout=0.05)
    assert val is None
    assert "timed out" in err
    assert tvcontrol._timed_out(err) is True


def test_subnet_broadcast():
    assert tvcontrol._subnet_broadcast("192.0.2.90") == "192.0.2.255"
    assert tvcontrol._subnet_broadcast("not-an-ip") == "255.255.255.255"


def test_token_path_uses_dir_and_sanitises_ip():
    assert tvcontrol.token_path("192.0.2.10", "/data/tokens") == "/data/tokens/192.0.2.10.txt"


def test_token_path_sanitises_unsafe_chars():
    assert tvcontrol.token_path("../evil ip", "/t") == "/t/.._evil_ip.txt"


def test_connect_opens_websocket_on_8002_when_it_works(tmp_path):
    # supported() is REST-only in samsungtvws 3.x; connect must exercise the
    # websocket via open(), not supported().
    fake = MagicMock()
    with patch("tvcontrol.ensure_awake", return_value={"woken": False}), \
         patch("tvcontrol.SamsungTVWS", return_value=fake) as ctor:
        art, port = tvcontrol.connect("1.2.3.4", token_dir=str(tmp_path))
    assert port == 8002
    assert ctor.call_args.kwargs["port"] == 8002
    fake.art.return_value.open.assert_called_once()
    fake.art.return_value.supported.assert_not_called()
    assert art is fake.art.return_value


def test_connect_falls_back_to_8001_when_8002_open_raises(tmp_path):
    good_art = MagicMock()

    def ctor_side_effect(**kwargs):
        m = MagicMock()
        if kwargs["port"] == 8002:
            m.art.return_value.open.side_effect = OSError("no ssl")
        else:
            m.art.return_value = good_art
        return m

    with patch("tvcontrol.ensure_awake", return_value={"woken": False}), \
         patch("tvcontrol.SamsungTVWS", side_effect=ctor_side_effect):
        art, port = tvcontrol.connect("1.2.3.4", token_dir=str(tmp_path))
    assert port == 8001
    assert art is good_art
    good_art.open.assert_called_once()


def test_connect_wakes_tv_before_opening(tmp_path):
    fake = MagicMock()
    with patch("tvcontrol.ensure_awake", return_value={"woken": True}) as wake, \
         patch("tvcontrol.SamsungTVWS", return_value=fake):
        tvcontrol.connect("1.2.3.4", token_dir=str(tmp_path), mac="AA:BB:CC:DD:EE:FF")
    wake.assert_called_once_with("1.2.3.4", "AA:BB:CC:DD:EE:FF")


def test_pair_paired_when_token_file_written(tmp_path):
    tf = tvcontrol.token_path("1.2.3.4", str(tmp_path))

    def write_token_on_open():
        os.makedirs(os.path.dirname(tf), exist_ok=True)
        with open(tf, "w") as fh:
            fh.write("REALTOKEN")

    fake = MagicMock()
    fake.art.return_value.open.side_effect = write_token_on_open
    with patch("tvcontrol.ensure_awake", return_value={"woken": False}), \
         patch("tvcontrol.SamsungTVWS", return_value=fake):
        result = tvcontrol.pair("1.2.3.4", token_dir=str(tmp_path))
    assert result["paired"] is True
    assert result["port"] == 8002
    assert result["error"] is None
    fake.art.return_value.open.assert_called_once()
    fake.art.return_value.close.assert_called_once()


def test_pair_not_paired_when_open_fails_and_no_token(tmp_path):
    fake = MagicMock()
    fake.art.return_value.open.side_effect = OSError("timeout waiting for popup")
    with patch("tvcontrol.ensure_awake", return_value={"woken": False}), \
         patch("tvcontrol.SamsungTVWS", return_value=fake):
        result = tvcontrol.pair("1.2.3.4", token_dir=str(tmp_path))
    assert result["paired"] is False
    assert result["error"] is not None


def test_apply_art_uploads_selects_deletes_old_and_closes(tmp_path):
    art = MagicMock()
    art.get_current.return_value = {"content_id": "OLD1"}
    art.upload.return_value = "NEW1"
    with patch("tvcontrol.connect", return_value=(art, 8002)):
        result = tvcontrol.apply_art("1.2.3.4", b"jpegbytes", token_dir=str(tmp_path))
    art.upload.assert_called_once()
    art.select_image.assert_called_once_with("NEW1")
    art.delete.assert_called_once_with("OLD1")
    art.close.assert_called_once()
    assert result["uploaded_id"] == "NEW1"
    assert result["port"] == 8002


def test_apply_art_tolerates_get_current_failure_and_still_closes(tmp_path):
    art = MagicMock()
    art.get_current.side_effect = RuntimeError("unsupported")
    art.upload.return_value = "NEW1"
    with patch("tvcontrol.connect", return_value=(art, 8001)):
        result = tvcontrol.apply_art("1.2.3.4", b"jpegbytes", token_dir=str(tmp_path))
    art.select_image.assert_called_once_with("NEW1")
    art.delete.assert_not_called()
    art.close.assert_called_once()
    assert result["uploaded_id"] == "NEW1"


def test_apply_art_closes_even_when_upload_raises(tmp_path):
    art = MagicMock()
    art.get_current.return_value = {"content_id": "OLD1"}
    art.upload.side_effect = RuntimeError("both matte attempts failed")
    with patch("tvcontrol.connect", return_value=(art, 8002)):
        try:
            tvcontrol.apply_art("1.2.3.4", b"jpegbytes", token_dir=str(tmp_path))
        except RuntimeError:
            pass
    art.close.assert_called_once()


def test_upload_retries_without_matte_when_first_fails():
    art = MagicMock()
    art.upload.side_effect = [RuntimeError("matte rejected"), "NEW2"]
    assert tvcontrol._upload(art, b"x") == "NEW2"
    assert art.upload.call_count == 2


def test_device_mac_falls_back_to_cache_when_rest_fails(tmp_path):
    import device_cache
    device_cache.remember("1.2.3.4", {"mac": "AA:BB:CC:DD:EE:FF"}, str(tmp_path))
    m = MagicMock()
    m.rest_device_info.side_effect = OSError("fully off")
    with patch("tvcontrol.SamsungTVWS", return_value=m):
        mac = tvcontrol._device_mac("1.2.3.4", token_dir=str(tmp_path))
    assert mac == "AA:BB:CC:DD:EE:FF"


def test_device_mac_prefers_live_rest_over_cache(tmp_path):
    import device_cache
    device_cache.remember("1.2.3.4", {"mac": "CACHED"}, str(tmp_path))
    m = MagicMock()
    m.rest_device_info.return_value = {"device": {"wifiMac": "LIVE"}}
    with patch("tvcontrol.SamsungTVWS", return_value=m):
        mac = tvcontrol._device_mac("1.2.3.4", token_dir=str(tmp_path))
    assert mac == "LIVE"


def test_ensure_awake_noop_when_already_awake():
    with patch("tvcontrol._port_open", return_value=True), \
         patch("tvcontrol.wol.send_magic_packet") as magic:
        result = tvcontrol.ensure_awake("1.2.3.4")
    assert result == {"woken": False, "reason": "already awake"}
    magic.assert_not_called()


def test_ensure_awake_returns_reason_when_no_mac():
    with patch("tvcontrol._port_open", return_value=False), \
         patch("tvcontrol._device_mac", return_value=None), \
         patch("tvcontrol.wol.send_magic_packet") as magic:
        result = tvcontrol.ensure_awake("1.2.3.4")
    assert result["woken"] is False
    assert "MAC" in result["reason"]
    magic.assert_not_called()


def test_ensure_awake_sends_wol_and_succeeds_when_tv_comes_up():
    # asleep on the initial check, awake after the magic packet
    port_states = iter([False, False, True])
    with patch("tvcontrol._port_open", side_effect=lambda *a, **k: next(port_states)), \
         patch("tvcontrol._device_mac", return_value="AA:BB:CC:DD:EE:FF"), \
         patch("tvcontrol.time.sleep"), \
         patch("tvcontrol.time.monotonic", return_value=0), \
         patch("tvcontrol.wol.send_magic_packet") as magic:
        result = tvcontrol.ensure_awake("1.2.3.4")
    assert result == {"woken": True, "mac": "AA:BB:CC:DD:EE:FF"}
    # sends to the TV's /24 directed broadcast (and the global broadcast too)
    first = magic.call_args_list[0]
    assert first.args[0] == "AA:BB:CC:DD:EE:FF"
    assert first.kwargs.get("broadcast") == "1.2.3.255"


def test_ensure_awake_times_out_when_tv_never_wakes():
    with patch("tvcontrol._port_open", return_value=False), \
         patch("tvcontrol.time.sleep"), \
         patch("tvcontrol.time.monotonic", side_effect=[0, 5, 31]), \
         patch("tvcontrol.wol.send_magic_packet") as magic:
        result = tvcontrol.ensure_awake("1.2.3.4", mac="AA:BB:CC:DD:EE:FF")
    assert result["woken"] is False
    assert "timed out" in result["reason"]
    assert magic.call_count >= 1


def test_state_unreachable_when_rest_fails():
    m = MagicMock()
    m.rest_device_info.side_effect = OSError("no route to host")
    with patch("tvcontrol.SamsungTVWS", return_value=m), \
         patch("tvcontrol._port_open", return_value=False):
        result = tvcontrol.state("1.2.3.4")
    assert result["reachable"] is False
    assert result["power"] == "off"
    assert result["summary"] == "off"
    assert result["art_mode"] == "unknown"
    # structurally identical to the reachable response
    assert set(result) == {"reachable", "power", "art_mode", "summary", "model", "name", "error"}


def test_state_off_falls_back_to_cached_model_and_name(tmp_path):
    import device_cache
    device_cache.remember("1.2.3.4", {"modelName": "QE65LS03R", "name": "[TV] Kitchen"}, str(tmp_path))
    m = MagicMock()
    m.rest_device_info.side_effect = OSError("off")
    with patch("tvcontrol.SamsungTVWS", return_value=m), \
         patch("tvcontrol._port_open", return_value=False):
        result = tvcontrol.state("1.2.3.4", token_dir=str(tmp_path))
    assert result["reachable"] is False
    assert result["model"] == "QE65LS03R"
    assert result["name"] == "[TV] Kitchen"


def test_state_remembers_device_for_later(tmp_path):
    import device_cache
    m = MagicMock()
    m.rest_device_info.return_value = {"device": {"PowerState": "on", "modelName": "X", "name": "Lounge"}}
    with patch("tvcontrol.SamsungTVWS", return_value=m), \
         patch("tvcontrol._port_open", return_value=False):
        tvcontrol.state("9.9.9.9", token_dir=str(tmp_path))
    assert device_cache.recall("9.9.9.9", str(tmp_path))["name"] == "Lounge"


def test_state_standby_reports_power_without_touching_websocket():
    m = MagicMock()
    m.rest_device_info.return_value = {"device": {"PowerState": "standby", "modelName": "QE65LS03R"}}
    with patch("tvcontrol.SamsungTVWS", return_value=m), \
         patch("tvcontrol._port_open", return_value=False), \
         patch("tvcontrol._read_artmode") as read_art:
        result = tvcontrol.state("1.2.3.4")
    assert result["reachable"] is True
    assert result["power"] == "standby"
    assert result["art_mode"] == "unknown"
    read_art.assert_not_called()  # never query ws when the TV is asleep


def test_state_on_with_art_mode_active(tmp_path):
    m = MagicMock()
    m.rest_device_info.return_value = {"device": {"PowerState": "on", "modelName": "X"}}
    with patch("tvcontrol.SamsungTVWS", return_value=m), \
         patch("tvcontrol._port_open", return_value=True), \
         patch("tvcontrol._read_artmode", return_value=("on", None)):
        result = tvcontrol.state("1.2.3.4", token_dir=str(tmp_path))
    assert result["power"] == "on"
    assert result["art_mode"] == "on"
    assert result["summary"] == "art mode"
    # same shape as the off response
    assert set(result) == {"reachable", "power", "art_mode", "summary", "model", "name", "error"}


def test_state_on_watching_when_art_mode_off(tmp_path):
    m = MagicMock()
    m.rest_device_info.return_value = {"device": {"PowerState": "on"}}
    with patch("tvcontrol.SamsungTVWS", return_value=m), \
         patch("tvcontrol._port_open", return_value=True), \
         patch("tvcontrol._read_artmode", return_value=("off", None)):
        result = tvcontrol.state("1.2.3.4", token_dir=str(tmp_path))
    assert result["summary"] == "on (watching)"


def test_diagnose_collects_rest_and_both_ports(tmp_path):
    rest_tv = MagicMock()
    rest_tv.rest_device_info.return_value = {
        "device": {"modelName": "UE43LS003", "FrameTVSupport": "true", "TokenAuthSupport": "true"}
    }

    def ctor(**kwargs):
        m = MagicMock()
        if "port" not in kwargs:
            return rest_tv
        art = m.art.return_value
        art.supported.return_value = kwargs["port"] == 8002
        art.get_api_version.return_value = "4.3.4.0"
        art.get_current.return_value = {"content_id": "MY-C0002"}
        return m

    with patch("tvcontrol.SamsungTVWS", side_effect=ctor):
        report = tvcontrol.diagnose("1.2.3.4", token_dir=str(tmp_path))

    assert report["rest"]["modelName"] == "UE43LS003"
    assert report["ports"]["8002"]["supported"] is True
    assert report["ports"]["8001"]["supported"] is False


def test_record_and_read_last_photo(tmp_path):
    assert tvcontrol.last_photo("1.2.3.4", token_dir=str(tmp_path)) is None
    tvcontrol.record_photo("1.2.3.4", "photo-9", token_dir=str(tmp_path))
    assert tvcontrol.last_photo("1.2.3.4", token_dir=str(tmp_path)) == "photo-9"


def test_record_photo_ignores_empty_id(tmp_path):
    tvcontrol.record_photo("1.2.3.4", None, token_dir=str(tmp_path))
    assert tvcontrol.last_photo("1.2.3.4", token_dir=str(tmp_path)) is None


def test_apply_art_logs_upload(monkeypatch, caplog):
    import tvcontrol

    class _Art:
        def get_current(self): return {"content_id": "OLD"}
        def upload(self, *a, **k): return "NEW-1"
        def select_image(self, *a, **k): return None
        def delete(self, *a, **k): return None
        def close(self): pass

    monkeypatch.setattr(tvcontrol, "connect", lambda ip, td, mac=None: (_Art(), 8002))
    with caplog.at_level("INFO"):
        result = tvcontrol.apply_art("1.2.3.4", b"jpeg")
    assert result == {"uploaded_id": "NEW-1", "port": 8002}
    assert any("uploaded=NEW-1" in r.message and "8002" in r.message for r in caplog.records)
