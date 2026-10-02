"""
Tests for Party Mode web server.

Tests server lifecycle, routes, WebSocket connections, authentication,
and MCP non-interference.
"""

import asyncio
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from dm20_protocol.claudmaster.pc_tracking import PCRegistry, MultiPlayerConfig
from dm20_protocol.models import Character
from dm20_protocol.permissions import PermissionResolver, PlayerRole
from dm20_protocol.party.server import (
    PartyServer,
    ConnectionManager,
    start_party_server,
    stop_party_server,
    get_server_instance,
)


@pytest.fixture
def pc_registry() -> PCRegistry:
    """Create a test PCRegistry."""
    config = MultiPlayerConfig(max_players=6)
    registry = PCRegistry(config)

    # Register some test PCs
    registry.join_session("aragorn", "Player1", PlayerRole.PLAYER)
    registry.join_session("legolas", "Player2", PlayerRole.PLAYER)
    registry.join_session("OBSERVER", "Observer1", PlayerRole.OBSERVER)

    return registry


@pytest.fixture
def permission_resolver() -> PermissionResolver:
    """Create a test PermissionResolver."""
    resolver = PermissionResolver()

    # Set up roles and ownership
    resolver.set_player_role("aragorn", PlayerRole.PLAYER)
    resolver.set_player_role("legolas", PlayerRole.PLAYER)
    resolver.set_player_role("OBSERVER", PlayerRole.OBSERVER)

    resolver.register_character_ownership("aragorn", "aragorn")
    resolver.register_character_ownership("legolas", "legolas")

    return resolver


@pytest.fixture
def mock_storage() -> MagicMock:
    """Create a mock StorageManager."""
    from dm20_protocol.models import CharacterClass, Race, AbilityScore

    storage = MagicMock()

    # Mock character data
    aragorn = Character(
        name="Aragorn",
        race=Race(name="Human"),
        character_class=CharacterClass(name="Ranger", level=5),
        armor_class=16,
        hit_points_max=45,
        hit_points_current=45,
        abilities={
            "strength": AbilityScore(score=16),
            "dexterity": AbilityScore(score=14),
            "constitution": AbilityScore(score=14),
            "intelligence": AbilityScore(score=10),
            "wisdom": AbilityScore(score=13),
            "charisma": AbilityScore(score=12),
        },
    )

    def get_character_side_effect(char_id: str) -> Character:
        if char_id == "aragorn":
            return aragorn
        raise ValueError(f"Character {char_id} not found")

    storage.get_character.side_effect = get_character_side_effect

    return storage


@pytest.fixture
def campaign_dir() -> Path:
    """Create a temporary campaign directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def party_server(
    pc_registry: PCRegistry,
    permission_resolver: PermissionResolver,
    mock_storage: MagicMock,
    campaign_dir: Path,
) -> PartyServer:
    """Create a test PartyServer instance."""
    server = PartyServer(
        pc_registry=pc_registry,
        permission_resolver=permission_resolver,
        storage=mock_storage,
        campaign_dir=campaign_dir,
        host="127.0.0.1",
        port=8888,
    )

    # Generate some test tokens
    server.token_manager.generate_token("aragorn")
    server.token_manager.generate_token("legolas")
    server.token_manager.generate_token("OBSERVER")

    return server


class TestConnectionManager:
    """Tests for ConnectionManager."""

    @pytest.mark.anyio
    async def test_connect_and_disconnect(self) -> None:
        """Test connecting and disconnecting a WebSocket."""
        manager = ConnectionManager()
        mock_ws = MagicMock()

        async def mock_accept() -> None:
            pass

        mock_ws.accept = mock_accept

        await manager.connect("player1", mock_ws)
        assert "player1" in manager._connections
        assert mock_ws in manager._connections["player1"]

        manager.disconnect("player1", mock_ws)
        assert "player1" not in manager._connections

    @pytest.mark.anyio
    async def test_multiple_connections_same_player(self) -> None:
        """Test multiple connections for the same player."""
        manager = ConnectionManager()
        mock_ws1 = MagicMock()
        mock_ws2 = MagicMock()

        async def mock_accept() -> None:
            pass

        mock_ws1.accept = mock_accept
        mock_ws2.accept = mock_accept

        await manager.connect("player1", mock_ws1)
        await manager.connect("player1", mock_ws2)

        assert len(manager._connections["player1"]) == 2
        assert manager.connection_count("player1") == 2

        manager.disconnect("player1", mock_ws1)
        assert len(manager._connections["player1"]) == 1

        manager.disconnect("player1", mock_ws2)
        assert "player1" not in manager._connections

    @pytest.mark.anyio
    async def test_send_to_player(self) -> None:
        """Test sending message to a specific player."""
        manager = ConnectionManager()
        mock_ws = MagicMock()

        async def mock_accept() -> None:
            pass

        async def mock_send_json(msg: dict) -> None:
            pass

        mock_ws.accept = mock_accept
        mock_ws.send_json = mock_send_json

        await manager.connect("player1", mock_ws)

        message = {"type": "test", "data": "hello"}
        sent = await manager.send_to_player("player1", message)

        assert sent == 1

    @pytest.mark.anyio
    async def test_broadcast(self) -> None:
        """Test broadcasting to all connected players."""
        manager = ConnectionManager()
        mock_ws1 = MagicMock()
        mock_ws2 = MagicMock()

        async def mock_accept() -> None:
            pass

        async def mock_send_json(msg: dict) -> None:
            pass

        mock_ws1.accept = mock_accept
        mock_ws2.accept = mock_accept
        mock_ws1.send_json = mock_send_json
        mock_ws2.send_json = mock_send_json

        await manager.connect("player1", mock_ws1)
        await manager.connect("player2", mock_ws2)

        message = {"type": "broadcast", "data": "hello all"}
        sent = await manager.broadcast(message)

        assert sent == 2

    def test_get_connected_players(self) -> None:
        """Test getting list of connected players."""
        manager = ConnectionManager()

        assert manager.get_connected_players() == []

    def test_connection_count(self) -> None:
        """Test total connection count."""
        manager = ConnectionManager()

        assert manager.connection_count() == 0
        assert manager.connection_count("player1") == 0


class TestPartyServerRoutes:
    """Tests for PartyServer HTTP routes."""

    def test_get_play_valid_token(self, party_server: PartyServer) -> None:
        """Test GET /play with valid token."""
        client = TestClient(party_server.app)
        token = party_server.token_manager.get_all_tokens()["aragorn"]

        response = client.get(f"/play?token={token}")

        assert response.status_code == 200
        assert "Party Mode" in response.text
        assert "aragorn" in response.text

    def test_get_play_missing_token(self, party_server: PartyServer) -> None:
        """Test GET /play without token."""
        client = TestClient(party_server.app)

        response = client.get("/play")

        assert response.status_code == 401
        assert "Missing token" in response.text

    def test_get_play_invalid_token(self, party_server: PartyServer) -> None:
        """Test GET /play with invalid token."""
        client = TestClient(party_server.app)

        response = client.get("/play?token=invalid123")

        assert response.status_code == 401
        assert "Invalid token" in response.text

    def test_get_map_serves_campaign_map(self, party_server: PartyServer, campaign_dir: Path) -> None:
        """GET /map serves maps/index.html, wrapped in a full document."""
        (campaign_dir / "maps").mkdir()
        (campaign_dir / "maps" / "index.html").write_text("<title>Vale</title><p>The Vale</p>")
        client = TestClient(party_server.app)
        token = party_server.token_manager.get_all_tokens()["aragorn"]

        response = client.get(f"/map?token={token}")

        assert response.status_code == 200
        assert response.text.lower().startswith("<!doctype html>")
        assert "<p>The Vale</p>" in response.text

    def test_get_map_requires_valid_token(self, party_server: PartyServer, campaign_dir: Path) -> None:
        """GET /map is token-gated like /play."""
        (campaign_dir / "maps").mkdir()
        (campaign_dir / "maps" / "index.html").write_text("<p>secret-ish</p>")
        client = TestClient(party_server.app)

        assert client.get("/map").status_code == 401
        assert client.get("/map?token=invalid123").status_code == 401

    def test_get_map_404_without_map(self, party_server: PartyServer) -> None:
        """GET /map returns 404 when the campaign has no map yet."""
        client = TestClient(party_server.app)
        token = party_server.token_manager.get_all_tokens()["aragorn"]

        assert client.get(f"/map?token={token}").status_code == 404

    def test_play_shows_map_button_only_when_map_exists(
        self, party_server: PartyServer, campaign_dir: Path
    ) -> None:
        """The player page links to /map with the player's own token, only if a map exists."""
        client = TestClient(party_server.app)
        token = party_server.token_manager.get_all_tokens()["aragorn"]

        before = client.get(f"/play?token={token}").text
        assert 'id="map-btn"' not in before
        assert "{map_link}" not in before

        (campaign_dir / "maps").mkdir()
        (campaign_dir / "maps" / "index.html").write_text("<p>map</p>")
        after = client.get(f"/play?token={token}").text
        assert f'href="/map?token={token}"' in after

    def test_post_action_valid_token(self, party_server: PartyServer) -> None:
        """Test POST /action with valid token."""
        client = TestClient(party_server.app)
        token = party_server.token_manager.get_all_tokens()["aragorn"]

        response = client.post(
            "/action",
            json={"action": "I attack the orc"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["player_id"] == "aragorn"

    def test_post_action_missing_token(self, party_server: PartyServer) -> None:
        """Test POST /action without token."""
        client = TestClient(party_server.app)

        response = client.post("/action", json={"action": "test"})

        assert response.status_code == 401

    def test_post_action_invalid_json(self, party_server: PartyServer) -> None:
        """Test POST /action with invalid JSON."""
        client = TestClient(party_server.app)
        token = party_server.token_manager.get_all_tokens()["aragorn"]

        response = client.post(
            "/action",
            data="not json",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 400

    def test_get_character_authorized(
        self,
        party_server: PartyServer,
        mock_storage: MagicMock,
    ) -> None:
        """Test GET /character/{player_id} with proper authorization."""
        client = TestClient(party_server.app)
        token = party_server.token_manager.get_all_tokens()["aragorn"]

        response = client.get(f"/character/aragorn?token={token}")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Aragorn"
        assert data["classes"][0]["name"] == "Ranger"

    def test_get_character_unauthorized(self, party_server: PartyServer) -> None:
        """Test GET /character/{player_id} without authorization."""
        client = TestClient(party_server.app)
        legolas_token = party_server.token_manager.get_all_tokens()["legolas"]

        # Legolas trying to access Aragorn's character
        response = client.get(f"/character/aragorn?token={legolas_token}")

        # get_character is ALLOWED for all roles, so this should succeed
        # (The permission check is for modifying, not reading)
        assert response.status_code == 200

    def test_get_character_missing_token(self, party_server: PartyServer) -> None:
        """Test GET /character/{player_id} without token."""
        client = TestClient(party_server.app)

        response = client.get("/character/aragorn")

        assert response.status_code == 401

    def test_get_status(self, party_server: PartyServer) -> None:
        """Test GET /status."""
        client = TestClient(party_server.app)

        response = client.get("/status")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert "uptime_seconds" in data
        assert "connected_players" in data
        assert data["active_pcs"] == 3  # aragorn, legolas, OBSERVER


class TestPartyServerWebSocket:
    """Tests for PartyServer WebSocket endpoint."""

    def test_websocket_valid_token(self, party_server: PartyServer) -> None:
        """Test WebSocket connection with valid token."""
        client = TestClient(party_server.app)
        token = party_server.token_manager.get_all_tokens()["aragorn"]

        with client.websocket_connect(f"/ws?token={token}") as websocket:
            # First message is the join broadcast
            join_msg = websocket.receive_json()
            assert join_msg["type"] == "system"
            assert "joined" in join_msg["content"]

            # Second message is the connection confirmation
            data = websocket.receive_json()
            assert data["type"] == "connected"
            assert data["player_id"] == "aragorn"

    def test_websocket_invalid_token(self, party_server: PartyServer) -> None:
        """Test WebSocket connection with invalid token."""
        client = TestClient(party_server.app)

        with pytest.raises(Exception):
            # Should close connection immediately
            with client.websocket_connect("/ws?token=invalid123"):
                pass

    def test_websocket_missing_token(self, party_server: PartyServer) -> None:
        """Test WebSocket connection without token."""
        client = TestClient(party_server.app)

        with pytest.raises(Exception):
            with client.websocket_connect("/ws"):
                pass

    def test_websocket_heartbeat(self, party_server: PartyServer) -> None:
        """Test WebSocket heartbeat message."""
        client = TestClient(party_server.app)
        token = party_server.token_manager.get_all_tokens()["aragorn"]

        with client.websocket_connect(f"/ws?token={token}") as websocket:
            # Receive connection confirmation
            websocket.receive_json()

            # Send heartbeat
            websocket.send_json({"type": "heartbeat"})

            # Should not crash or return error
            # (In real implementation, heartbeat updates pc_registry)


class TestServerLifecycle:
    """Tests for server start/stop lifecycle."""

    def test_start_server(
        self,
        pc_registry: PCRegistry,
        permission_resolver: PermissionResolver,
        mock_storage: MagicMock,
        campaign_dir: Path,
    ) -> None:
        """Test starting the Party Mode server."""
        import httpx

        server = start_party_server(
            pc_registry=pc_registry,
            permission_resolver=permission_resolver,
            storage=mock_storage,
            campaign_dir=campaign_dir,
            host="127.0.0.1",
            port=8889,
        )

        try:
            assert server is not None
            assert get_server_instance() is not None

            # Give server time to fully start
            time.sleep(1)

            # Test that it's actually running by making a request
            response = httpx.get("http://127.0.0.1:8889/status", timeout=2)
            assert response.status_code == 200

        finally:
            stop_party_server()

    def test_start_server_already_running(
        self,
        pc_registry: PCRegistry,
        permission_resolver: PermissionResolver,
        mock_storage: MagicMock,
        campaign_dir: Path,
    ) -> None:
        """Test starting server when already running raises error."""
        start_party_server(
            pc_registry=pc_registry,
            permission_resolver=permission_resolver,
            storage=mock_storage,
            campaign_dir=campaign_dir,
            host="127.0.0.1",
            port=8890,
        )

        try:
            with pytest.raises(RuntimeError, match="already running"):
                start_party_server(
                    pc_registry=pc_registry,
                    permission_resolver=permission_resolver,
                    storage=mock_storage,
                    campaign_dir=campaign_dir,
                    host="127.0.0.1",
                    port=8890,
                )
        finally:
            stop_party_server()

    def test_stop_server(
        self,
        pc_registry: PCRegistry,
        permission_resolver: PermissionResolver,
        mock_storage: MagicMock,
        campaign_dir: Path,
    ) -> None:
        """Test stopping the Party Mode server."""
        start_party_server(
            pc_registry=pc_registry,
            permission_resolver=permission_resolver,
            storage=mock_storage,
            campaign_dir=campaign_dir,
            host="127.0.0.1",
            port=8891,
        )

        time.sleep(1)
        stop_party_server()

        # Should be stopped now
        assert get_server_instance() is None

    def test_stop_server_not_running(self) -> None:
        """Test stopping server when not running raises error."""
        with pytest.raises(RuntimeError, match="not running"):
            stop_party_server()


class TestMCPNonInterference:
    """Tests to ensure web server doesn't interfere with MCP stdio."""

    def test_server_runs_in_background_thread(
        self,
        pc_registry: PCRegistry,
        permission_resolver: PermissionResolver,
        mock_storage: MagicMock,
        campaign_dir: Path,
    ) -> None:
        """Test that server runs in a daemon background thread."""
        import threading

        main_thread_id = threading.current_thread().ident

        server = start_party_server(
            pc_registry=pc_registry,
            permission_resolver=permission_resolver,
            storage=mock_storage,
            campaign_dir=campaign_dir,
            host="127.0.0.1",
            port=8892,
        )

        try:
            # Find the server thread
            server_thread = None
            for thread in threading.enumerate():
                if thread.name == "PartyModeServer":
                    server_thread = thread
                    break

            assert server_thread is not None
            assert server_thread.daemon is True
            assert server_thread.ident != main_thread_id
            assert server_thread.is_alive()

        finally:
            stop_party_server()

    def test_main_thread_not_blocked(
        self,
        pc_registry: PCRegistry,
        permission_resolver: PermissionResolver,
        mock_storage: MagicMock,
        campaign_dir: Path,
    ) -> None:
        """Test that starting server doesn't block main thread."""
        start_time = time.time()

        start_party_server(
            pc_registry=pc_registry,
            permission_resolver=permission_resolver,
            storage=mock_storage,
            campaign_dir=campaign_dir,
            host="127.0.0.1",
            port=8893,
        )

        elapsed = time.time() - start_time

        try:
            # Should return almost immediately (< 1 second)
            assert elapsed < 1.5

            # Main thread should be able to do work
            result = sum(range(1000))
            assert result == 499500

        finally:
            stop_party_server()


class TestServerEdgeCases:
    """Edge case tests for Party Mode server."""

    def test_stable_token_after_refresh(self, party_server: PartyServer) -> None:
        """Refresh rotates the (secret-derived) token; the new one works."""
        client = TestClient(party_server.app)

        old_token = party_server.token_manager.get_all_tokens()["aragorn"]
        new_token = party_server.token_manager.refresh_token("aragorn")

        assert old_token != new_token

        # Token still works
        response = client.get(f"/play?token={new_token}")
        assert response.status_code == 200

    def test_observer_access(self, party_server: PartyServer) -> None:
        """Test that OBSERVER role has appropriate access."""
        client = TestClient(party_server.app)
        token = party_server.token_manager.get_all_tokens()["OBSERVER"]

        # OBSERVER should be able to access /play
        response = client.get(f"/play?token={token}")
        assert response.status_code == 200

        # OBSERVER SHOULD be able to read character data (get_character is ALLOWED for all)
        response = client.get(f"/character/aragorn?token={token}")
        assert response.status_code == 200
