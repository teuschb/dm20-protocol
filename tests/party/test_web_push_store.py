"""Tests for Web Push subscription storage and per-device sending."""

import json
from unittest.mock import MagicMock, patch

from pywebpush import WebPushException

import pytest

from dm20_protocol.party.push import (
    DEFAULT_VAPID_SUBJECT,
    PushSender,
    PushSubscriptionStore,
    get_vapid_subject,
    validate_push_endpoint,
)


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


GOOD_ENDPOINTS = [
    "https://fcm.googleapis.com/fcm/send/abc",
    "https://updates.push.services.mozilla.com/wpush/v2/abc",
    "https://web.push.apple.com/QWxs",
    "https://db5p.notify.windows.com/w/?token=abc",
]
BAD_ENDPOINTS = [
    "http://fcm.googleapis.com/fcm/send/abc",
    "https://169.254.169.254/latest/meta-data",
    "https://localhost:8080/x",
    "https://fcm.googleapis.com.evil.example/x",
    "https://evilfcm.googleapis.com@evil.example/x",
    "https://user:pw@fcm.googleapis.com/x",
    "https://fcm.googleapis.com:8443/x",
    "https://notpush.apple.com/x",
    "ftp://web.push.apple.com/x",
    "",
    None,
    123,
]


@pytest.mark.parametrize("endpoint", GOOD_ENDPOINTS)
def test_known_push_hosts_accepted(endpoint):
    assert validate_push_endpoint(endpoint) is None


@pytest.mark.parametrize("endpoint", BAD_ENDPOINTS)
def test_other_endpoints_rejected(endpoint):
    assert validate_push_endpoint(endpoint)


def test_subscribe_route_rejects_ssrf_endpoint(tmp_path):
    from starlette.testclient import TestClient

    from dm20_protocol.party.server import PartyServer
    from unittest.mock import MagicMock

    server = PartyServer(
        pc_registry=MagicMock(), permission_resolver=MagicMock(), storage=MagicMock(),
        campaign_dir=tmp_path, host="127.0.0.1", port=8899,
    )
    token = server.token_manager.generate_token("aragorn")
    client = TestClient(server.app)
    keys = {"p256dh": "k", "auth": "a"}

    bad = client.post(f"/push/subscribe?token={token}",
                      json={"endpoint": "http://127.0.0.1:9/x", "keys": keys})
    assert bad.status_code == 400 and "https" in bad.json()["error"]
    other = client.post(f"/push/subscribe?token={token}",
                        json={"endpoint": "https://evil.example/x", "keys": keys})
    assert other.status_code == 400
    assert server.push_store.all() == {}

    ok = client.post(f"/push/subscribe?token={token}",
                     json={"endpoint": "https://web.push.apple.com/abc", "keys": keys})
    assert ok.status_code == 200
    assert len(server.push_store.all()["aragorn"]) == 1


def test_vapid_subject_default_and_env_override(monkeypatch):
    monkeypatch.delenv("DM20_VAPID_SUBJECT", raising=False)
    assert get_vapid_subject() == DEFAULT_VAPID_SUBJECT
    monkeypatch.setenv("DM20_VAPID_SUBJECT", "mailto:me@mydomain.org")
    assert get_vapid_subject() == "mailto:me@mydomain.org"
    monkeypatch.setenv("DM20_VAPID_SUBJECT", "https://mydomain.org/contact")
    assert get_vapid_subject() == "https://mydomain.org/contact"
    monkeypatch.setenv("DM20_VAPID_SUBJECT", "dm@localhost")
    assert get_vapid_subject() == DEFAULT_VAPID_SUBJECT


def test_send_uses_configured_vapid_subject(tmp_path, monkeypatch):
    monkeypatch.setenv("DM20_VAPID_SUBJECT", "mailto:me@mydomain.org")
    store = PushSubscriptionStore(tmp_path / "subs.json")
    sender = PushSender(MagicMock(), store)
    with patch("dm20_protocol.party.push.webpush") as wp:
        assert sender._send_one("p1", _sub("phone"), {"title": "t"})
    assert wp.call_args.kwargs["vapid_claims"] == {"sub": "mailto:me@mydomain.org"}
