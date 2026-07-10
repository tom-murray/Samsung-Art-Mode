"""Wake-on-LAN: send a magic packet to wake a sleeping TV.

Samsung Frame TVs (esp. 2019/LS03R) close their websocket control ports while
in standby; only the REST endpoint answers. A magic packet brings the TV back
so the websocket comes up.
"""
import socket


def normalise_mac(mac: str) -> bytes:
    """Return the 6 raw bytes of a MAC given in any common textual form."""
    if not isinstance(mac, str):
        raise ValueError(f"Invalid MAC address: {mac!r}")
    hexstr = (
        mac.replace(":", "").replace("-", "").replace(".", "").replace(" ", "").strip()
    )
    if len(hexstr) != 12:
        raise ValueError(f"Invalid MAC address: {mac!r}")
    try:
        return bytes.fromhex(hexstr)
    except ValueError:
        raise ValueError(f"Invalid MAC address: {mac!r}")


def build_magic_packet(mac: str) -> bytes:
    """6 bytes of 0xFF followed by the target MAC repeated 16 times (102 bytes)."""
    return b"\xff" * 6 + normalise_mac(mac) * 16


def send_magic_packet(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    packet = build_magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))
