import json
import os

import device_cache


def test_recall_missing_returns_empty(tmp_path):
    assert device_cache.recall("1.1.1.1", str(tmp_path)) == {}


def test_remember_and_recall_roundtrip_and_persists(tmp_path):
    device_cache.remember("1.1.1.1", {"modelName": "M", "name": "N", "wifiMac": None}, str(tmp_path))
    # None values are dropped
    assert device_cache.recall("1.1.1.1", str(tmp_path)) == {"modelName": "M", "name": "N"}
    # written to disk (survives a fresh read / container rebuild)
    with open(os.path.join(str(tmp_path), "device-cache.json")) as f:
        assert json.load(f)["1.1.1.1"]["modelName"] == "M"


def test_remember_merges_over_existing(tmp_path):
    device_cache.remember("1.1.1.1", {"modelName": "M"}, str(tmp_path))
    device_cache.remember("1.1.1.1", {"name": "N"}, str(tmp_path))
    assert device_cache.recall("1.1.1.1", str(tmp_path)) == {"modelName": "M", "name": "N"}


def test_remember_ignores_all_none(tmp_path):
    device_cache.remember("1.1.1.1", {"modelName": None, "name": None}, str(tmp_path))
    assert device_cache.recall("1.1.1.1", str(tmp_path)) == {}


def test_corrupt_cache_file_is_tolerated(tmp_path):
    with open(os.path.join(str(tmp_path), "device-cache.json"), "w") as f:
        f.write("{not json")
    assert device_cache.recall("1.1.1.1", str(tmp_path)) == {}
    # a subsequent write still works
    device_cache.remember("1.1.1.1", {"name": "N"}, str(tmp_path))
    assert device_cache.recall("1.1.1.1", str(tmp_path)) == {"name": "N"}
