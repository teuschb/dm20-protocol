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
        out = _call(private_messages='{"bardchar": "Pick your College when ready."}')
    assert "Pushed" in out
    tts.assert_not_called()
    assert server.response_queue.get_for_player("bardchar")[0]["private"] == "Pick your College when ready."
    assert "private" not in server.response_queue.get_for_player("fighterchar")[0]


def test_malformed_private_json_is_rejected_not_dropped(tmp_path):
    server, _ = _server(tmp_path)
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server):
        out = _call(narrative="x", private_messages='{"bardchar": "bad "quote"}')
    assert out.startswith("Not sent")
    assert server.response_queue.get_for_player("bardchar") == []


def test_empty_push_rejected(tmp_path):
    server, _ = _server(tmp_path)
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server):
        assert _call(narrative="  ").startswith("Not sent")


def test_party_mode_not_running():
    with patch("dm20_protocol.party.server.get_server_instance", return_value=None):
        assert _call(narrative="hi") == "Party Mode is not running."


def _resolve(**kwargs):
    fn = getattr(main.party_resolve_action, "fn", main.party_resolve_action)
    return fn(**kwargs)


def _resolve_server(tmp_path):
    server, pushed = _server(tmp_path)
    action_id = server.action_queue.push("bardchar", "look")
    return server, action_id


def test_non_object_private_json_is_rejected_by_push(tmp_path):
    server, _ = _server(tmp_path)
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server):
        out = _call(narrative="", private_messages='["bardchar", "secret"]')
    assert out.startswith("Not sent") and "JSON object" in out


def test_resolve_action_rejects_malformed_private_json(tmp_path):
    server, action_id = _resolve_server(tmp_path)
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server), \
         patch.object(main, "_party_tts_speak"):
        out = _resolve(action_id=action_id, narrative="ok", private_messages='{"bardchar": oops}')
    assert out.startswith("Not resolved") and "not valid JSON" in out
    assert server.response_queue.get_for_player("bardchar") == []


def test_resolve_action_rejects_non_object_private_json(tmp_path):
    server, action_id = _resolve_server(tmp_path)
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server), \
         patch.object(main, "_party_tts_speak"):
        out = _resolve(action_id=action_id, narrative="ok", private_messages='["bardchar"]')
    assert out.startswith("Not resolved") and "JSON object" in out
    assert server.response_queue.get_for_player("bardchar") == []


def test_resolve_action_still_delivers_valid_private_messages(tmp_path):
    server, action_id = _resolve_server(tmp_path)
    with patch("dm20_protocol.party.server.get_server_instance", return_value=server), \
         patch.object(main, "_party_tts_speak"):
        out = _resolve(action_id=action_id, narrative="ok", private_messages='{"bardchar": "psst"}')
    assert out.startswith("Response broadcast")
    assert server.response_queue.get_for_player("bardchar")[0]["private"] == "psst"
