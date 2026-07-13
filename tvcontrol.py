import logging
import os
import re
import socket
import threading
import time

from samsungtvws import SamsungTVWS

import device_cache
import wol

log = logging.getLogger(__name__)

DEFAULT_TOKEN_DIR = os.environ.get("TOKEN_DIR", "/data/tokens")
# How many recently-shown photos to remember per TV and exclude from the next
# pick, so the rotation doesn't repeat itself.
HISTORY_SIZE = int(os.environ.get("HISTORY_SIZE", "20"))
CONTROL_PORTS = (8002, 8001)
# Some TVs (e.g. 2019 Frame) accept a websocket connection but never answer an
# art request, and the underlying library can wait forever. Cap every art call.
ART_CALL_TIMEOUT = 12.0
# REST answers even in standby, but requests.get(timeout=None) never returns if
# the TV stalls, so give the REST lookups their own bound too.
REST_TIMEOUT = 8.0
_TIMEOUT_MARKER = "timed out after"


def _call_bounded(fn, timeout: float = ART_CALL_TIMEOUT):
    """Run fn() with a hard wall-clock cap. Returns (value, error_repr).

    If fn hangs past `timeout`, returns (None, "timed out ...") rather than
    blocking forever. The worker thread is a daemon; closing the connection
    afterwards unblocks its stuck recv().
    """
    box = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - report anything, never leave box empty
            box["error"] = repr(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, f"{_TIMEOUT_MARKER} {timeout}s"
    return box.get("value"), box.get("error")


def _timed_out(err) -> bool:
    return bool(err) and _TIMEOUT_MARKER in err


def token_path(ip: str, token_dir: str = None) -> str:
    token_dir = token_dir or DEFAULT_TOKEN_DIR
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", ip)
    return os.path.join(token_dir, f"{safe}.txt")


def _port_open(ip: str, port: int, timeout: float = 2.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def _device_mac(ip: str, token_dir: str = None):
    """Read the TV's MAC from live REST, falling back to the cached value.

    REST answers in standby but not when the TV is fully off — in which case we
    use the MAC remembered from when it was last online, so Wake-on-LAN can still
    be attempted.
    """
    info, _ = _safe(lambda: SamsungTVWS(host=ip, timeout=REST_TIMEOUT).rest_device_info())
    device = (info or {}).get("device", {}) if info else {}
    mac = device.get("wifiMac") or device.get("mac")
    if mac:
        return mac
    return device_cache.recall(ip, token_dir or DEFAULT_TOKEN_DIR).get("mac")


def _subnet_broadcast(ip: str) -> str:
    """Best-effort /24 directed broadcast for the TV's subnet (e.g. 192.0.2.255).

    A directed broadcast is more likely than 255.255.255.255 to reach the TV on
    a segmented/VLAN network. Falls back to the global broadcast on odd input.
    """
    parts = ip.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return ".".join(parts[:3] + ["255"])
    return "255.255.255.255"


def ensure_awake(ip: str, mac: str = None, wait: float = 30, poll: float = 2.0):
    """Wake the TV via Wake-on-LAN if its control ports are closed (standby).

    Older Frame TVs shut their websocket ports in standby, so any control call
    hangs or is refused. Returns a dict describing the outcome; never raises.
    """
    if any(_port_open(ip, p) for p in CONTROL_PORTS):
        return {"woken": False, "reason": "already awake"}

    mac = mac or _device_mac(ip)
    if not mac:
        return {"woken": False, "reason": "TV asleep and no MAC available for Wake-on-LAN"}

    log.info("TV %s appears asleep; sending Wake-on-LAN to %s", ip, mac)
    broadcast = _subnet_broadcast(ip)
    deadline = time.monotonic() + wait
    while True:
        try:
            wol.send_magic_packet(mac, broadcast=broadcast)
            if broadcast != "255.255.255.255":
                wol.send_magic_packet(mac)  # also the global broadcast, belt and braces
        except Exception as e:  # noqa: BLE001
            return {"woken": False, "reason": f"Wake-on-LAN failed: {e!r}", "mac": mac}
        time.sleep(poll)
        if any(_port_open(ip, p) for p in CONTROL_PORTS):
            return {"woken": True, "mac": mac}
        if time.monotonic() >= deadline:
            return {"woken": False, "reason": "timed out waiting for TV to wake", "mac": mac}


def _safe_close(art) -> None:
    try:
        art.close()
    except Exception as e:  # noqa: BLE001
        log.debug("Closing art connection failed: %r", e)


def _open_art(ip: str, port: int, token_dir: str, timeout: float):
    """Build an art client on `port` and open its websocket.

    ``art.open()`` performs the real websocket handshake — unlike ``supported()``,
    which is a REST-only capability check in samsungtvws 3.x. On port 8002 the
    handshake is what shows the "Allow" popup and, once accepted, the library
    writes the returned token to the token file.
    """
    kwargs = {"host": ip, "port": port, "timeout": timeout}
    if port == 8002:
        kwargs["token_file"] = token_path(ip, token_dir)
    art = SamsungTVWS(**kwargs).art()
    # websocket-client's timeout is per-recv; art.open()'s frame loop can still
    # spin forever if the TV dribbles non-ready frames. Bound the whole open.
    _, err = _call_bounded(art.open, timeout)
    if err:
        _safe_close(art)
        raise ConnectionError(f"art.open on port {port} failed: {err}")
    return art


def connect(ip: str, token_dir: str = None, timeout: float = 10, mac: str = None):
    """Return (art, port) with an OPEN websocket. Caller must close the art client.

    Wakes the TV first (Wake-on-LAN) if it is asleep, then tries 8002 (SSL +
    token) and falls back to 8001 (legacy, no popup).
    """
    tdir = token_dir or DEFAULT_TOKEN_DIR
    os.makedirs(tdir, exist_ok=True)
    ensure_awake(ip, mac)
    try:
        return _open_art(ip, 8002, tdir, timeout), 8002
    except Exception as e:  # noqa: BLE001
        log.warning("Port 8002 failed for %s (%r); falling back to 8001", ip, e)
        return _open_art(ip, 8001, tdir, timeout), 8001


def pair(ip: str, token_dir: str = None, timeout: float = 30, mac: str = None):
    """Open a 8002 websocket so the TV shows the Allow popup; wait for acceptance.

    Wakes the TV first if asleep. Success is signalled by the token file being
    written — the library saves the captured token there (not on the client
    object) whenever a token_file is set.
    """
    tdir = token_dir or DEFAULT_TOKEN_DIR
    os.makedirs(tdir, exist_ok=True)
    tf = token_path(ip, tdir)
    wake = ensure_awake(ip, mac)
    error = None
    try:
        art = _open_art(ip, 8002, tdir, timeout)
        _safe_close(art)
    except Exception as e:  # noqa: BLE001 - report, do not raise
        error = repr(e)
        log.warning("Pairing %s did not complete: %r", ip, e)
    have_token = os.path.exists(tf) and os.path.getsize(tf) > 0
    return {
        "paired": have_token,
        "port": 8002,
        "token_file": tf,
        "wake": wake,
        "error": error,
    }


def _upload(art, image_bytes: bytes) -> str:
    """Upload JPEG bytes. Older art APIs reject matte='none'; retry with default matte."""
    try:
        return art.upload(image_bytes, file_type="JPEG", matte="none")
    except Exception as e:  # noqa: BLE001
        log.warning("upload matte='none' failed (%r); retrying with default matte", e)
        return art.upload(image_bytes, file_type="JPEG")


class _ArtSession:
    """An open art connection whose calls are individually time-bounded.

    If a bounded call times out, its worker thread is still blocked reading the
    shared socket, so the next call on that socket could race it. To avoid that
    we close the poisoned connection and reconnect before the next operation.
    """

    def __init__(self, ip: str, token_dir: str, mac: str):
        self._ip, self._token_dir, self._mac = ip, token_dir, mac
        self.art, self.port = connect(ip, token_dir, mac=mac)

    def run(self, fn, timeout: float = ART_CALL_TIMEOUT):
        value, err = _call_bounded(lambda: fn(self.art), timeout)
        if _timed_out(err):
            _safe_close(self.art)  # unblock the stuck reader, then start clean
            self.art, self.port = connect(self._ip, self._token_dir, mac=self._mac)
        return value, err

    def close(self):
        _safe_close(self.art)


def apply_art(ip: str, image_bytes: bytes, token_dir: str = None, mac: str = None):
    session = _ArtSession(ip, token_dir, mac)
    try:
        current, cur_err = session.run(lambda a: a.get_current())
        if cur_err:
            log.warning("get_current failed on %s (%s); skipping delete-old", ip, cur_err)

        uploaded_id, up_err = session.run(lambda a: _upload(a, image_bytes), timeout=120)
        if up_err or not uploaded_id:
            raise RuntimeError(f"Uploading art to {ip} failed: {up_err or 'no id returned'}")

        _, sel_err = session.run(lambda a: a.select_image(uploaded_id))
        if sel_err:
            raise RuntimeError(f"Selecting new art on {ip} failed: {sel_err}")

        if current and current.get("content_id"):
            _, del_err = session.run(lambda a: a.delete(current["content_id"]))
            if del_err:
                log.warning("delete old image failed on %s (%s)", ip, del_err)

        log.info("uploaded=%s port=%s", uploaded_id, session.port)
        return {"uploaded_id": uploaded_id, "port": session.port}
    finally:
        session.close()


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs), None
    except Exception as e:  # noqa: BLE001
        return None, repr(e)


def _probe_port(ip: str, port: int, token_dir: str):
    kwargs = {"host": ip, "port": port, "timeout": 10}
    if port == 8002:
        kwargs["token_file"] = token_path(ip, token_dir)
    art = SamsungTVWS(**kwargs).art()
    try:
        supported, sup_err = _safe(art.supported)  # REST capability flag
        version, ver_err = _call_bounded(art.get_api_version)  # websocket, capped
        current, cur_err = _call_bounded(art.get_current)  # websocket, capped
        return {
            "supported": supported,
            "api_version": version,
            "current": current,
            "errors": {k: v for k, v in {
                "supported": sup_err, "api_version": ver_err, "current": cur_err,
            }.items() if v},
        }
    finally:
        _safe_close(art)


def diagnose(ip: str, token_dir: str = None):
    tdir = token_dir or DEFAULT_TOKEN_DIR
    os.makedirs(tdir, exist_ok=True)

    info, rest_err = _safe(lambda: SamsungTVWS(host=ip, timeout=REST_TIMEOUT).rest_device_info())
    device = (info or {}).get("device", {}) if info else {}
    if device:
        _remember_device(ip, device, tdir)
    rest = {
        "modelName": device.get("modelName"),
        "name": device.get("name"),
        "FrameTVSupport": device.get("FrameTVSupport"),
        "TokenAuthSupport": device.get("TokenAuthSupport"),
        "PowerState": device.get("PowerState"),
        "mac": device.get("wifiMac") or device.get("mac"),
        "error": rest_err,
    }

    ports = {}
    for port in (8002, 8001):
        result, err = _safe(_probe_port, ip, port, tdir)
        ports[str(port)] = result if result else {"error": err}

    return {"ip": ip, "rest": rest, "ports": ports}


def _read_artmode(ip: str, token_dir: str):
    """Best-effort art-mode status ("on"/"off") over the websocket.

    Does NOT wake the TV — callers must only invoke this when a control port is
    already open. Returns (value, error_repr).
    """
    for port in CONTROL_PORTS:
        try:
            art = _open_art(ip, port, token_dir, timeout=8)
        except Exception:  # noqa: BLE001 - try the next port
            continue
        try:
            value, err = _call_bounded(art.get_artmode)
            if err:
                return "unknown", err
            return (str(value).lower() if value is not None else "unknown"), None
        finally:
            _safe_close(art)
    return "unknown", "no control port reachable"


def _summarise_state(power: str, art_mode: str) -> str:
    if art_mode == "on":
        return "art mode"
    if art_mode == "off" and power == "on":
        return "on (watching)"
    if power == "on":
        return "on"
    if power == "standby":
        return "standby (Frame TVs usually display art in this state)"
    return power or "unknown"


def state(ip: str, token_dir: str = None):
    """Report a TV's power and art-mode state. Read-only: never wakes the TV.

    - power: "on" | "standby" | "unreachable" (from REST, which answers in standby)
    - art_mode: "on" | "off" | "unknown" (websocket query; "unknown" when the TV
      is asleep, since older Frames shut the socket in standby)
    """
    tdir = token_dir or DEFAULT_TOKEN_DIR
    info, rest_err = _safe(lambda: SamsungTVWS(host=ip, timeout=REST_TIMEOUT).rest_device_info())
    if rest_err or not info:
        # A fully-off Frame TV isn't on the network, so REST failing == off (we
        # can't distinguish that from a transient network drop; reachable:false
        # is preserved so a consumer can debounce if it wants). Fall back to the
        # last-known model/name so the caller still knows which TV this is.
        cached = device_cache.recall(ip, tdir)
        return {
            "reachable": False,
            "power": "off",
            "art_mode": "unknown",
            "summary": "off",
            "model": cached.get("modelName"),
            "name": cached.get("name"),
            "error": rest_err,
        }

    device = info.get("device", {}) if isinstance(info, dict) else {}
    power = device.get("PowerState") or "unknown"
    _remember_device(ip, device, tdir)

    art_mode, art_err = "unknown", None
    if any(_port_open(ip, p) for p in CONTROL_PORTS):
        os.makedirs(tdir, exist_ok=True)
        art_mode, art_err = _read_artmode(ip, tdir)

    return {
        "reachable": True,
        "power": power,
        "art_mode": art_mode,
        "summary": _summarise_state(power, art_mode),
        "model": device.get("modelName"),
        "name": device.get("name"),
        "error": art_err,
    }


def recent_photos(ip: str, token_dir: str = None) -> list:
    """The last HISTORY_SIZE photo ids shown on this TV, oldest first."""
    return device_cache.recall(ip, token_dir or DEFAULT_TOKEN_DIR).get("recent_photos") or []


def record_photo(ip: str, photo_id, token_dir: str = None) -> None:
    """Append photo_id to this TV's rolling history (deduped, capped at
    HISTORY_SIZE) so recently-shown photos can be excluded from the next pick."""
    if not photo_id:
        return
    tdir = token_dir or DEFAULT_TOKEN_DIR
    history = [pid for pid in recent_photos(ip, tdir) if pid != photo_id]
    history.append(photo_id)
    device_cache.remember(ip, {"recent_photos": history[-HISTORY_SIZE:]}, tdir)


def _remember_device(ip: str, device: dict, token_dir: str) -> None:
    device_cache.remember(ip, {
        "modelName": device.get("modelName"),
        "name": device.get("name"),
        "mac": device.get("wifiMac") or device.get("mac"),
    }, token_dir)
