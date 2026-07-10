from unittest.mock import MagicMock, patch

import wol


def test_normalise_mac_accepts_common_forms():
    expected = bytes.fromhex("aabbccddeeff")
    assert wol.normalise_mac("AA:BB:CC:DD:EE:FF") == expected
    assert wol.normalise_mac("aabbccddeeff") == expected
    assert wol.normalise_mac("AA-BB-CC-DD-EE-FF") == expected


def test_normalise_mac_rejects_bad_input():
    for bad in ("", "AA:BB:CC", "ZZ:BB:CC:DD:EE:FF", "AABBCCDDEEFFGG", None, 12345):
        try:
            wol.normalise_mac(bad)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_build_magic_packet_structure():
    packet = wol.build_magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:12] == bytes.fromhex("aabbccddeeff")
    # the MAC repeats 16 times
    assert packet == b"\xff" * 6 + bytes.fromhex("aabbccddeeff") * 16


def test_send_magic_packet_broadcasts_udp():
    fake_sock = MagicMock()
    with patch("wol.socket.socket", return_value=fake_sock) as ctor:
        # context-manager form
        fake_sock.__enter__.return_value = fake_sock
        wol.send_magic_packet("AA:BB:CC:DD:EE:FF")
    ctor.assert_called_once_with(wol.socket.AF_INET, wol.socket.SOCK_DGRAM)
    fake_sock.setsockopt.assert_called_once_with(
        wol.socket.SOL_SOCKET, wol.socket.SO_BROADCAST, 1
    )
    sent_packet, addr = fake_sock.sendto.call_args.args
    assert sent_packet == wol.build_magic_packet("AA:BB:CC:DD:EE:FF")
    assert addr == ("255.255.255.255", 9)
