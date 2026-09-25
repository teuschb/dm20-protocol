"""
End-to-end integration tests for Party Mode.

Tests the full pipeline: action submission -> queue -> processing ->
response push -> WebSocket delivery, plus token lifecycle, reconnection,
combat integration, queue persistence, and session stability.
"""

import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from dm20_protocol.claudmaster.pc_tracking import MultiPlayerConfig, PCRegistry
from dm20_protocol.permissions import PermissionResolver, PlayerRole
from dm20_protocol.party.server import PartyServer

from .conftest import ALL_IDS, CHARACTERS, PLAYER_IDS
from .helpers import SimulatedPlayer, make_players


# ===================================================================
# Scenario 1: Full Session Flow
# ===================================================================


class TestFullSessionFlow:
    """Verify the complete action → response → delivery cycle."""

    def test_action_submit_queue_and_resolve(
        self, e2e_server: PartyServer, e2e_tokens: dict[str, str]
    ) -> None:
        """Player submits action -> queued -> host pops -> resolves."""
        client = TestClient(e2e_server.app)
        token = e2e_tokens["thorin"]

        # 1. Submit action via HTTP
        resp = client.post(
            "/action",
            json={"action": "I search the room"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        action_id = data["action_id"]
        assert data["player_id"] == "thorin"
        assert data["status"] == "pending"

        # 2. Check action status
        status_resp = client.get(f"/action/{action_id}/status")
        assert status_resp.json()["status"] == "pending"

        # 3. Host pops the action (simulates /dm:party-next)
        action = e2e_server.action_queue.pop()
        assert action is not None
        assert action["id"] == action_id
        assert action["player_id"] == "thorin"
        assert action["text"] == "I search the room"

        # 4. Action is now processing
        status_resp = client.get(f"/action/{action_id}/status")
        assert status_resp.json()["status"] == "processing"

        # 5. Host resolves the action
        e2e_server.action_queue.resolve(action_id, {"narrative": "You find a trap"})

        status_resp = client.get(f"/action/{action_id}/status")
        assert status_resp.json()["status"] == "resolved"

    def test_multiple_players_submit_sequentially(
        self, e2e_server: PartyServer
    ) -> None:
        """4 players submit actions in sequence, all queue correctly."""
        players = make_players(e2e_server, PLAYER_IDS)

        action_ids = []
        for pid in PLAYER_IDS:
            result = players[pid].submit_action(f"{pid} attacks")
            assert result["success"] is True
            action_ids.append(result["action_id"])

        # All 4 should be pending
        assert e2e_server.action_queue.get_pending_count() == 4

        # Pop in FIFO order
        for i, pid in enumerate(PLAYER_IDS):
            action = e2e_server.action_queue.pop()
            assert action is not None
            assert action["player_id"] == pid
            assert action["id"] == action_ids[i]

    def test_response_push_filters_by_role(
        self, e2e_server: PartyServer
    ) -> None:
        """Push response with private + dm_only, verify filtering."""
        response = {
            "narrative": "The door opens with a creak.",
            "private": {
                "thorin": "You notice a trap on the door.",
                "vex": "Your rogue instincts warn you of danger.",
            },
            "dm_only": "The trap deals 2d6 fire damage.",
            "action_id": "act_0001",
        }

        resp_id = e2e_server.response_queue.push(response)
        assert resp_id.startswith("res_")

        # Verify filtering for each role
        thorin_msgs = e2e_server.response_queue.get_for_player("thorin")
        assert len(thorin_msgs) == 1
        assert thorin_msgs[0]["narrative"] == "The door opens with a creak."
        assert thorin_msgs[0]["private"] == "You notice a trap on the door."
        assert "dm_only" not in thorin_msgs[0]
        assert "all_private" not in thorin_msgs[0]

        elara_msgs = e2e_server.response_queue.get_for_player("elara")
        assert len(elara_msgs) == 1
        assert elara_msgs[0]["narrative"] == "The door opens with a creak."
        assert "private" not in elara_msgs[0]
        assert "dm_only" not in elara_msgs[0]

        observer_msgs = e2e_server.response_queue.get_for_player("OBSERVER")
        assert len(observer_msgs) == 1
        assert observer_msgs[0]["narrative"] == "The door opens with a creak."
        assert "private" not in observer_msgs[0]
        assert "dm_only" not in observer_msgs[0]

    def test_websocket_connection_and_messages(
        self, e2e_server: PartyServer, e2e_tokens: dict[str, str]
    ) -> None:
        """Player connects via WebSocket and receives confirmation."""
        client = TestClient(e2e_server.app)
        token = e2e_tokens["thorin"]

        with client.websocket_connect(f"/ws?token={token}") as ws:
            # First: system broadcast (join)
            join_msg = ws.receive_json()
            assert join_msg["type"] == "system"
            assert "joined" in join_msg["content"]

            # Second: connection confirmation
            conn_msg = ws.receive_json()
            assert conn_msg["type"] == "connected"
            assert conn_msg["player_id"] == "thorin"

    def test_websocket_action_via_ws_message(
        self, e2e_server: PartyServer, e2e_tokens: dict[str, str]
    ) -> None:
        """Player submits action via WebSocket message type."""
        client = TestClient(e2e_server.app)
        token = e2e_tokens["elara"]

        with client.websocket_connect(f"/ws?token={token}") as ws:
            # Consume initial messages
            ws.receive_json()  # system join
            ws.receive_json()  # connected

            # Send action via WebSocket
            ws.send_json({"type": "action", "text": "I cast detect magic"})

            # Receive action status confirmation
            status_msg = ws.receive_json()
            assert status_msg["type"] == "action_status"
            assert status_msg["status"] == "pending"
            assert status_msg["action_id"].startswith("act_")

        # Verify action is in queue
        assert e2e_server.action_queue.get_pending_count() == 1


# ===================================================================
# Scenario 2: Token Security
# ===================================================================


class TestTokenSecurity:
    """Token validation across all endpoints."""

    def test_invalid_token_rejected_http(
        self, e2e_server: PartyServer
    ) -> None:
        """Invalid token rejected on all HTTP endpoints."""
        client = TestClient(e2e_server.app)

        # GET /play
        resp = client.get("/play?token=INVALID")
        assert resp.status_code == 401

        # POST /action
        resp = client.post(
            "/action",
            json={"action": "test"},
            headers={"Authorization": "Bearer INVALID"},
        )
        assert resp.status_code == 401

        # GET /character
        resp = client.get("/character/thorin?token=INVALID")
        assert resp.status_code == 401

    def test_invalid_token_rejected_websocket(
        self, e2e_server: PartyServer
    ) -> None:
        """Invalid token rejected on WebSocket endpoint."""
        client = TestClient(e2e_server.app)

        with pytest.raises(Exception):
            with client.websocket_connect("/ws?token=INVALID"):
                pass

    def test_missing_token_rejected_websocket(
        self, e2e_server: PartyServer
    ) -> None:
        """Missing token rejected on WebSocket endpoint."""
        client = TestClient(e2e_server.app)

        with pytest.raises(Exception):
            with client.websocket_connect("/ws"):
                pass

    def test_token_refresh_returns_stable_token(
        self, e2e_server: PartyServer
    ) -> None:
        """Refresh rotates the token (old link dies) and the new one works."""
        client = TestClient(e2e_server.app)
        old_token = e2e_server.token_manager.get_all_tokens()["thorin"]

        new_token = e2e_server.token_manager.refresh_token("thorin")
        assert new_token != old_token
        assert client.get(f"/play?token={old_token}").status_code != 200

        # Token still works after refresh
        resp = client.get(f"/play?token={new_token}")
        assert resp.status_code == 200

        resp = client.post(
            "/action",
            json={"action": "test"},
            headers={"Authorization": f"Bearer {new_token}"},
        )
        assert resp.status_code == 200

    def test_revoked_token_rejected(
        self, e2e_server: PartyServer
    ) -> None:
        """Kicked player's token is rejected everywhere."""
        client = TestClient(e2e_server.app)
        token = e2e_server.token_manager.get_all_tokens()["gorm"]

        # Verify token works first
        resp = client.get(f"/play?token={token}")
        assert resp.status_code == 200

        # Revoke (kick)
        e2e_server.token_manager.revoke_token("gorm")

        # Token should fail on all endpoints
        resp = client.get(f"/play?token={token}")
        assert resp.status_code == 401

        resp = client.post(
            "/action",
            json={"action": "test"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

        resp = client.get(f"/character/gorm?token={token}")
        assert resp.status_code == 401

    def test_kick_does_not_affect_others(
        self, e2e_server: PartyServer
    ) -> None:
        """Revoking one player's token doesn't affect others."""
        client = TestClient(e2e_server.app)

        # Revoke gorm
        e2e_server.token_manager.revoke_token("gorm")

        # Other players should still work
        for pid in ["thorin", "elara", "vex"]:
            token = e2e_server.token_manager.get_all_tokens()[pid]
            resp = client.get(f"/play?token={token}")
            assert resp.status_code == 200, f"{pid}'s token should still work"


# ===================================================================
# Scenario 3: Reconnection and Message Replay
# ===================================================================


class TestReconnection:
    """Test reconnection with message replay."""

    def test_reconnect_replays_missed_messages(
        self, e2e_server: PartyServer, e2e_tokens: dict[str, str]
    ) -> None:
        """Player disconnects, misses messages, reconnects and receives them."""
        client = TestClient(e2e_server.app)
        token = e2e_tokens["thorin"]

        # Push 5 responses (thorin will "miss" these)
        timestamps = []
        for i in range(5):
            resp_id = e2e_server.response_queue.push({
                "narrative": f"Message {i + 1}",
            })
            all_resps = e2e_server.response_queue.get_all()
            timestamps.append(all_resps[-1]["timestamp"])

        # Get all messages for thorin (no filter) to verify they exist
        all_msgs = e2e_server.response_queue.get_for_player("thorin")
        assert len(all_msgs) == 5

        # Request replay since before the first message (gets all 5)
        # Use a timestamp before the first message
        since_before = "2020-01-01T00:00:00.000000Z"
        replayed = e2e_server.response_queue.get_for_player(
            "thorin", since_timestamp=since_before
        )
        assert len(replayed) == 5

        # Request replay since the 3rd message timestamp (gets last 2)
        replayed_partial = e2e_server.response_queue.get_for_player(
            "thorin", since_timestamp=timestamps[2]
        )
        assert len(replayed_partial) == 2
        assert replayed_partial[0]["narrative"] == "Message 4"
        assert replayed_partial[1]["narrative"] == "Message 5"

    def test_reconnect_filters_private_content(
        self,
        e2e_server: PartyServer,
    ) -> None:
        """Replayed messages still respect permission filtering."""
        # Push responses with private content for different players
        e2e_server.response_queue.push({
            "narrative": "Public narrative",
            "private": {"thorin": "Thorin secret", "elara": "Elara secret"},
            "dm_only": "DM notes",
        })

        # Thorin should see only their private content
        thorin_msgs = e2e_server.response_queue.get_for_player("thorin")
        assert thorin_msgs[0]["private"] == "Thorin secret"
        assert "dm_only" not in thorin_msgs[0]

        # Elara should see only their private content
        elara_msgs = e2e_server.response_queue.get_for_player("elara")
        assert elara_msgs[0]["private"] == "Elara secret"
        assert "dm_only" not in elara_msgs[0]

        # Vex should see no private content
        vex_msgs = e2e_server.response_queue.get_for_player("vex")
        assert "private" not in vex_msgs[0]

        # Observer should see no private content
        obs_msgs = e2e_server.response_queue.get_for_player("OBSERVER")
        assert "private" not in obs_msgs[0]
        assert "dm_only" not in obs_msgs[0]


# ===================================================================
# Scenario 4: Combat Lifecycle
# ===================================================================


class TestCombatLifecycle:
    """Test combat start, turn gating, and combat end."""

    def _setup_combat(self, server: PartyServer) -> MagicMock:
        """Set up a mock TurnManager in turn-based combat mode."""
        from dm20_protocol.claudmaster.turn_manager import (
            TurnDistribution,
            TurnPhase,
        )

        turn_manager = MagicMock()

        # Mock state for active turn-based combat
        state = MagicMock()
        state.phase = TurnPhase.COMBAT
        state.distribution_mode = TurnDistribution.ROUND_ROBIN
        state.turn_order = ["thorin", "elara", "vex", "gorm"]
        state.current_round = 1
        turn_manager.state = state

        # thorin goes first
        turn_manager.get_current_turn.return_value = "thorin"
        turn_manager.can_act.side_effect = lambda pid: pid == "thorin"

        # Initiative values
        turn_manager._combat_initiatives = {
            "thorin": 18, "elara": 15, "vex": 12, "gorm": 8
        }
        turn_manager._simultaneous_queue = []

        server.turn_manager = turn_manager
        return turn_manager

    def test_turn_gating_allows_current_player(
        self, e2e_server: PartyServer
    ) -> None:
        """Current turn player can submit actions."""
        self._setup_combat(e2e_server)
        players = make_players(e2e_server, PLAYER_IDS)

        # Thorin (current turn) should be allowed
        result = players["thorin"].submit_action("I attack the goblin")
        assert result.get("success") is True

    def test_turn_gating_blocks_other_players(
        self, e2e_server: PartyServer
    ) -> None:
        """Non-current-turn players are blocked from acting."""
        self._setup_combat(e2e_server)
        client = TestClient(e2e_server.app)

        # Elara, Vex, Gorm should be blocked (403)
        for pid in ["elara", "vex", "gorm"]:
            token = e2e_server.token_manager.get_all_tokens()[pid]
            resp = client.post(
                "/action",
                json={"action": "I try to act"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 403
            assert "Not your turn" in resp.json()["error"]

    def test_turn_advance_unblocks_next_player(
        self, e2e_server: PartyServer
    ) -> None:
        """After turn advance, next player can act."""
        turn_manager = self._setup_combat(e2e_server)
        players = make_players(e2e_server, PLAYER_IDS)

        # Advance to elara's turn
        turn_manager.get_current_turn.return_value = "elara"
        turn_manager.can_act.side_effect = lambda pid: pid == "elara"

        # Elara should now be allowed
        result = players["elara"].submit_action("I cast fireball")
        assert result.get("success") is True

        # Thorin should now be blocked
        result = players["thorin"].submit_action("I attack")
        assert result.get("error") is not None

    def test_no_turn_manager_allows_all(
        self, e2e_server: PartyServer
    ) -> None:
        """Without TurnManager, all players can act freely."""
        e2e_server.turn_manager = None
        players = make_players(e2e_server, PLAYER_IDS)

        for pid in PLAYER_IDS:
            result = players[pid].submit_action(f"{pid} acts freely")
            assert result.get("success") is True

    def test_combat_state_personalized(
        self, e2e_server: PartyServer, e2e_mock_storage: MagicMock
    ) -> None:
        """Each player sees personalized your_turn in combat state."""
        from dm20_protocol.party.bridge import get_combat_state

        turn_manager = self._setup_combat(e2e_server)

        thorin_state = get_combat_state("thorin", turn_manager, e2e_mock_storage)
        assert thorin_state is not None
        assert thorin_state["data"]["your_turn"] is True

        elara_state = get_combat_state("elara", turn_manager, e2e_mock_storage)
        assert elara_state is not None
        assert elara_state["data"]["your_turn"] is False


# ===================================================================
# Scenario 5: Character Update Propagation
# ===================================================================


class TestCharacterUpdatePropagation:
    """Test that character data changes are accessible."""

    def test_character_data_reflects_storage(
        self, e2e_server: PartyServer, e2e_tokens: dict[str, str]
    ) -> None:
        """GET /character returns current storage data."""
        client = TestClient(e2e_server.app)
        token = e2e_tokens["thorin"]

        resp = client.get(f"/character/thorin?token={token}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Thorin"
        assert data["hit_points_current"] == 52
        assert data["armor_class"] == 18


# ===================================================================
# Scenario 6: Queue Persistence
# ===================================================================


class TestQueuePersistence:
    """Test that queue state survives restart."""

    def test_action_queue_persists(self, e2e_campaign_dir: Path) -> None:
        """Actions survive queue recreation (simulates restart)."""
        from dm20_protocol.party.queue import ActionQueue

        # Create queue and push actions
        q1 = ActionQueue(e2e_campaign_dir)
        aid1 = q1.push("thorin", "I search the room")
        aid2 = q1.push("elara", "I cast detect magic")

        # Resolve one
        q1.pop()
        q1.resolve(aid1, {"narrative": "found"})

        # Create a new queue from the same directory (simulates restart)
        q2 = ActionQueue(e2e_campaign_dir)

        # Resolved action should stay resolved
        assert q2.get_status(aid1) == "resolved"

        # Pending action should be restored as pending
        assert q2.get_status(aid2) == "pending"
        assert q2.get_pending_count() == 1

        # Should be able to pop the restored action
        action = q2.pop()
        assert action is not None
        assert action["id"] == aid2
        assert action["player_id"] == "elara"

    def test_response_queue_persists(self, e2e_campaign_dir: Path) -> None:
        """Responses survive queue recreation."""
        from dm20_protocol.party.queue import ResponseQueue

        q1 = ResponseQueue(e2e_campaign_dir)
        q1.push({"narrative": "First message"})
        q1.push({"narrative": "Second message", "private": {"thorin": "secret"}})

        # Recreate from same dir
        q2 = ResponseQueue(e2e_campaign_dir)
        all_msgs = q2.get_all()
        assert len(all_msgs) == 2
        assert all_msgs[0]["narrative"] == "First message"
        assert all_msgs[1]["private"] == {"thorin": "secret"}

        # Filtering should still work
        thorin_msgs = q2.get_for_player("thorin")
        assert len(thorin_msgs) == 2
        assert thorin_msgs[1]["private"] == "secret"


# ===================================================================
# Scenario 7: Session Stability
# ===================================================================


class TestSessionStability:
    """Simulated sustained session: many actions, no crashes, no leaks."""

    def test_sustained_session(
        self, e2e_server: PartyServer
    ) -> None:
        """Simulate ~200 action/response cycles without errors."""
        players = make_players(e2e_server, PLAYER_IDS)
        iterations = 50  # 50 rounds x 4 players = 200 actions

        for i in range(iterations):
            # Each player submits an action
            action_ids = []
            for pid in PLAYER_IDS:
                result = players[pid].submit_action(f"Round {i}: {pid} acts")
                assert result["success"] is True
                action_ids.append(result["action_id"])

            # Host processes all actions
            for aid in action_ids:
                action = e2e_server.action_queue.pop()
                assert action is not None
                e2e_server.action_queue.resolve(
                    action["id"],
                    {"narrative": f"Resolved {action['id']}"},
                )

                # Push response
                e2e_server.response_queue.push({
                    "narrative": f"Response to {action['id']}",
                    "action_id": action["id"],
                })

        # Verify state
        assert e2e_server.action_queue.get_pending_count() == 0
        all_responses = e2e_server.response_queue.get_all()
        assert len(all_responses) == 200  # 50 rounds * 4 players


# ===================================================================
# Scenario 8: Server Status
# ===================================================================


class TestServerStatus:
    """Test server health endpoint."""

    def test_status_endpoint(
        self, e2e_server: PartyServer
    ) -> None:
        """GET /status returns correct server info."""
        client = TestClient(e2e_server.app)
        resp = client.get("/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert isinstance(data["uptime_seconds"], float)
        assert isinstance(data["connected_players"], list)
        assert data["active_pcs"] == 5  # 4 players + OBSERVER
