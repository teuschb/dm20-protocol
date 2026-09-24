"""Tests for the party_push MCP tool (DM-initiated push with no action_id)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from dm20_protocol import main
from dm20_protocol.party.queue import ActionQueue, ResponseQueue


def _call(**kwargs):
    fn = getattr(main.party_push, "fn", main.party_push)
    return fn(**kwargs)


def _server(tmp_path):
    pushed = []
    return SimpleNamespace(
        response_queue=ResponseQueue(tmp_path, on_push=pushed.append),
        action_queue=ActionQueue(tmp_path),
    ), pushed


def test_push_without_action_id_reaches_queue(tmp_path):
    server, pushed = _server(tmp_path)
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server), \
         patch.object(main, "_party_tts_speak"):
        out = _call(narrative="The dock bell tolls midnight.")
    assert "Pushed" in out
    rec = server.response_queue.get_for_player("p1")[0]
    assert rec["narrative"] == "The dock bell tolls midnight."
    assert rec.get("action_id") is None
    assert len(pushed) == 1  # on_push fired -> live broadcast path triggered


def test_private_only_push_targets_one_player(tmp_path):
    server, _ = _server(tmp_path)
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server), \
         patch.object(main, "_party_tts_speak") as tts:
        out = _call(private_messages='{"lou": "Pick your College when ready."}')
    assert "Pushed" in out
    tts.assert_not_called()
    assert server.response_queue.get_for_player("lou")[0]["private"] == "Pick your College when ready."
    assert "private" not in server.response_queue.get_for_player("iona")[0]


def test_malformed_private_json_is_rejected_not_dropped(tmp_path):
    server, _ = _server(tmp_path)
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server):
        out = _call(narrative="x", private_messages='{"lou": "bad "quote"}')
    assert out.startswith("Not sent")
    assert server.response_queue.get_for_player("lou") == []


def test_empty_push_rejected(tmp_path):
    server, _ = _server(tmp_path)
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server):
        assert _call(narrative="  ").startswith("Not sent")


def test_party_mode_not_running():
    with patch("dm20_protocol.party.server.get_server_instance", return_value=None):
        assert _call(narrative="hi") == "Party Mode is not running."
