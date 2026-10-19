"""Tests for device-emulation support (browser factory + API)."""

from __future__ import annotations

from src.browser.factory import DEVICE_DESCRIPTORS, resolve_device


def test_curated_devices_present() -> None:
    assert "iPhone 13" in DEVICE_DESCRIPTORS
    assert "Pixel 5" in DEVICE_DESCRIPTORS
    assert "iPad (gen 7)" in DEVICE_DESCRIPTORS
    assert "Desktop Chrome HiDPI" in DEVICE_DESCRIPTORS


def test_pixel_5_descriptor() -> None:
    d = resolve_device("Pixel 5")
    assert d is not None
    assert d["viewport"] == {"width": 393, "height": 851}
    assert d["is_mobile"] is True
    assert d["has_touch"] is True
    assert d["default_browser"] == "chromium"


def test_iphone_descriptor() -> None:
    d = resolve_device("iPhone 13")
    assert d is not None
    assert d["is_mobile"] is True
    assert d["default_browser"] == "webkit"


def test_unknown_device_returns_none() -> None:
    assert resolve_device("Nokia 3310") is None


def test_devices_api() -> None:
    from fastapi.testclient import TestClient

    from src.api.app import app

    client = TestClient(app)
    r = client.get("/api/devices")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 4
    names = {d["name"] for d in body["devices"]}
    assert "iPhone 13" in names
    assert "Pixel 5" in names
    # user_agent must NOT be leaked in the public device list
    assert all("user_agent" not in d for d in body["devices"])
