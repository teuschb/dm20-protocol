"""Tests for Web Push subscription storage and per-device sending."""

import json
from unittest.mock import MagicMock, patch

from pywebpush import WebPushException

from dm20_protocol.party.push import PushSender, PushSubscriptionStore


def _sub(name: str) -> dict:
    return {"endpoint": f"https://push.example/{name}", "keys": {"p256dh": "k", "auth": "a"}}


def test_second_device_does_not_replace_first(tmp_path):
    store = PushSubscriptionStore(tmp_path / "subs.json")
    store.add("p1", _sub("phone"))
    store.add("p1", _sub("laptop"))

    endpoints = [s["endpoint"] for s in store.all()["p1"]]
    assert endpoints == ["https://push.example/phone", "https://push.example/laptop"]


def test_resubscribing_same_device_refreshes_instead_of_duplicating(tmp_path):
    store = PushSubscriptionStore(tmp_path / "subs.json")
    store.add("p1", _sub("phone"))
    store.add("p1", _sub("laptop"))
    store.add("p1", _sub("phone"))

    endpoints = [s["endpoint"] for s in store.all()["p1"]]
    assert endpoints == ["https://push.example/laptop", "https://push.example/phone"]


def test_keeps_only_newest_devices_per_player(tmp_path):
    store = PushSubscriptionStore(tmp_path / "subs.json")
    for i in range(PushSubscriptionStore.MAX_PER_PLAYER + 2):
        store.add("p1", _sub(f"d{i}"))

    subs = store.all()["p1"]
    assert len(subs) == PushSubscriptionStore.MAX_PER_PLAYER
    assert subs[-1]["endpoint"] == f"https://push.example/d{PushSubscriptionStore.MAX_PER_PLAYER + 1}"


def test_upgrades_old_single_subscription_file(tmp_path):
    path = tmp_path / "subs.json"
    path.write_text(json.dumps({"p1": _sub("old")}))

    store = PushSubscriptionStore(path)
    assert store.all() == {"p1": [_sub("old")]}

    store.add("p1", _sub("new"))
    assert [s["endpoint"] for s in store.all()["p1"]] == [
        "https://push.example/old",
        "https://push.example/new",
    ]


def test_remove_one_device_or_all(tmp_path):
    store = PushSubscriptionStore(tmp_path / "subs.json")
    store.add("p1", _sub("phone"))
    store.add("p1", _sub("laptop"))

    store.remove("p1", "https://push.example/phone")
    assert [s["endpoint"] for s in store.all()["p1"]] == ["https://push.example/laptop"]

    store.remove("p1")
    assert "p1" not in store.all()


def test_persists_across_reload(tmp_path):
    path = tmp_path / "subs.json"
    PushSubscriptionStore(path).add("p1", _sub("phone"))
    assert PushSubscriptionStore(path).all() == {"p1": [_sub("phone")]}


def test_send_reaches_every_device_and_drops_gone_ones(tmp_path):
    store = PushSubscriptionStore(tmp_path / "subs.json")
    store.add("p1", _sub("phone"))
    store.add("p1", _sub("stale"))
    sender = PushSender(MagicMock(), store)

    gone = MagicMock(status_code=410)

    def fake_webpush(subscription_info, **kwargs):
        if subscription_info["endpoint"].endswith("stale"):
            raise WebPushException("gone", response=gone)

    with patch("dm20_protocol.party.push.webpush", side_effect=fake_webpush) as wp:
        assert sender.send_to("p1", {"title": "t", "body": "b"}) is True

    assert wp.call_count == 2
    assert [s["endpoint"] for s in store.all()["p1"]] == ["https://push.example/phone"]


def test_send_returns_false_when_no_device_accepts(tmp_path):
    store = PushSubscriptionStore(tmp_path / "subs.json")
    store.add("p1", _sub("phone"))
    sender = PushSender(MagicMock(), store)

    with patch("dm20_protocol.party.push.webpush", side_effect=RuntimeError("boom")):
        assert sender.send_to("p1", {"title": "t", "body": "b"}) is False
    assert sender.send_to("nobody", {"title": "t", "body": "b"}) is False
