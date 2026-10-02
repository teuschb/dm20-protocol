"""
Web server for Party Mode player UI.

Runs a Starlette/Uvicorn server in a background thread with its own asyncio
event loop. The server MUST NOT block the main thread (which runs the MCP
stdio transport).

Key components:
- Starlette app with routes for player UI and WebSocket connections
- Background thread lifecycle management (start/stop)
- WebSocket connection manager for real-time updates
- Token-based authentication middleware
- Integration with PCRegistry, PermissionResolver, and StorageManager

Routes:
- GET /play?token=xxx - Serve player UI (static HTML)
- POST /action - Submit player action
- GET /character/{player_id} - Get character data (with permission check)
- GET /status - Server health and connected players
- WS /ws?token=xxx - WebSocket connection for real-time updates
"""

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from dm20_protocol.claudmaster.pc_tracking import PCRegistry
from dm20_protocol.permissions import PermissionResolver
from dm20_protocol.storage import DnDStorage

from . import bridge
from .auth import TokenManager, detect_host_ip
from .push import PushKeys, PushSender, PushSubscriptionStore
from .queue import ActionQueue, ResponseQueue

if TYPE_CHECKING:
    from dm20_protocol.claudmaster.turn_manager import TurnManager
    from dm20_protocol.voice.streaming import AudioStreamManager

logger = logging.getLogger("dm20-protocol.party")


# Global server state
_server_thread: Optional[threading.Thread] = None
_server_instance: Optional["PartyServer"] = None
_stop_event: Optional[threading.Event] = None


class ConnectionManager:
    """
    Manages WebSocket connections for Party Mode.

    Tracks active connections per player_id. A player may have multiple
    tabs open, so we store a set of WebSocket connections per player.

    Attributes:
        _connections: Dict mapping player_id -> set of WebSocket connections
    """

    def __init__(self) -> None:
        """Initialize an empty ConnectionManager."""
        self._connections: dict[str, set[WebSocket]] = {}
        self._last_seen: dict[str, str] = {}
        self._last_pong: dict[str, float] = {}
        self._lock = threading.Lock()

    async def connect(self, player_id: str, websocket: WebSocket) -> None:
        """
        Register a new WebSocket connection for a player.

        Args:
            player_id: The player's identifier
            websocket: The WebSocket connection
        """
        await websocket.accept()
        with self._lock:
            if player_id not in self._connections:
                self._connections[player_id] = set()
            self._connections[player_id].add(websocket)
        logger.info(f"WebSocket connected: player_id={player_id} "
                   f"({len(self._connections[player_id])} total connections)")

    def disconnect(self, player_id: str, websocket: WebSocket) -> None:
        """
        Unregister a WebSocket connection for a player.

        Args:
            player_id: The player's identifier
            websocket: The WebSocket connection to remove
        """
        with self._lock:
            if player_id in self._connections:
                self._connections[player_id].discard(websocket)
                if not self._connections[player_id]:
                    del self._connections[player_id]
        logger.info(f"WebSocket disconnected: player_id={player_id}")

    async def send_to_player(self, player_id: str, message: dict) -> int:
        """
        Send a JSON message to all connections for a specific player.

        Args:
            player_id: The player to send to
            message: The JSON-serializable message dict

        Returns:
            Number of connections the message was sent to
        """
        with self._lock:
            connections = self._connections.get(player_id, set()).copy()

        sent = 0
        for ws in connections:
            try:
                await ws.send_json(message)
                sent += 1
            except Exception as e:
                logger.warning(f"Failed to send message to {player_id}: {e}")
        return sent

    async def broadcast(self, message: dict) -> int:
        """
        Broadcast a JSON message to all connected players.

        Args:
            message: The JSON-serializable message dict

        Returns:
            Number of connections the message was sent to
        """
        with self._lock:
            all_connections = []
            for conns in self._connections.values():
                all_connections.extend(conns)

        sent = 0
        for ws in all_connections:
            try:
                await ws.send_json(message)
                sent += 1
            except Exception as e:
                logger.warning(f"Failed to broadcast message: {e}")
        return sent

    def get_connected_players(self) -> list[str]:
        """
        Get a list of all currently connected player IDs.

        Returns:
            List of player_id strings
        """
        with self._lock:
            return list(self._connections.keys())

    def connection_count(self, player_id: Optional[str] = None) -> int:
        """
        Get the number of connections for a player, or total if player_id is None.

        Args:
            player_id: The player to count connections for, or None for total

        Returns:
            Number of connections
        """
        with self._lock:
            if player_id is not None:
                return len(self._connections.get(player_id, set()))
            return sum(len(conns) for conns in self._connections.values())

    async def close_all(self) -> None:
        """Close all WebSocket connections gracefully."""
        with self._lock:
            all_connections = []
            for conns in self._connections.values():
                all_connections.extend(conns)
            self._connections.clear()

        for ws in all_connections:
            try:
                await ws.close()
            except Exception as e:
                logger.warning(f"Error closing WebSocket: {e}")
        logger.info(f"Closed {len(all_connections)} WebSocket connections")

    async def broadcast_response(
        self,
        response: dict,
        permission_resolver: PermissionResolver,
    ) -> int:
        """
        Push a response to all connected players with per-player filtering.

        Each player receives a personalized view: public narrative for all,
        private messages only for the intended recipient, dm_only stripped
        for non-DM players.

        Args:
            response: The raw response dict from ResponseQueue
            permission_resolver: Used to determine player roles

        Returns:
            Total number of WebSocket sends
        """
        with self._lock:
            player_ids = list(self._connections.keys())

        total_sent = 0
        for player_id in player_ids:
            filtered = bridge.format_response(response, player_id, permission_resolver)
            # Wrap as a WebSocket message with type
            ws_msg = {"type": "narrative", **filtered}
            if "private" in filtered:
                ws_msg["type"] = "private"
                ws_msg["from"] = "DM"
            sent = await self.send_to_player(player_id, ws_msg)
            total_sent += sent

            # If there's a private message for this player, send it separately
            if "private" in filtered and filtered.get("narrative"):
                # Send narrative as a separate message
                narrative_msg = {
                    "type": "narrative",
                    "id": filtered.get("id"),
                    "timestamp": filtered.get("timestamp"),
                    "content": filtered.get("narrative", ""),
                }
                private_msg = {
                    "type": "private",
                    "id": filtered.get("id"),
                    "timestamp": filtered.get("timestamp"),
                    "content": filtered["private"],
                    "from": "DM",
                }
                # Replace the combined message with two separate ones
                # (The client expects separate narrative and private messages)

        return total_sent

    async def broadcast_combat_state(
        self,
        turn_manager: "TurnManager",
        storage: DnDStorage,
        permission_resolver: PermissionResolver,
    ) -> int:
        """
        Broadcast personalized combat state to all connected players.

        Each player receives their own view with ``your_turn`` set
        correctly for their character.

        Args:
            turn_manager: Active TurnManager instance
            storage: Campaign storage for character data lookups
            permission_resolver: Used to determine player roles

        Returns:
            Total number of WebSocket sends
        """
        with self._lock:
            player_ids = list(self._connections.keys())

        total_sent = 0
        for player_id in player_ids:
            combat_msg = bridge.get_combat_state(
                player_id, turn_manager, storage
            )
            if combat_msg is not None:
                sent = await self.send_to_player(player_id, combat_msg)
                total_sent += sent

        return total_sent

    async def handle_reconnect(
        self,
        player_id: str,
        since_timestamp: Optional[str],
        response_queue: "ResponseQueue",
        permission_resolver: PermissionResolver,
    ) -> int:
        """
        Replay missed messages since a player's last-seen timestamp.

        Args:
            player_id: The reconnecting player
            since_timestamp: ISO timestamp of last seen message
            response_queue: Queue to fetch missed responses from
            permission_resolver: For filtering

        Returns:
            Number of replayed messages
        """
        from dm20_protocol.permissions import PlayerRole

        role = permission_resolver.get_player_role(player_id)
        is_dm = role == PlayerRole.DM

        missed = response_queue.get_for_player(
            player_id,
            since_timestamp=since_timestamp,
            is_dm=is_dm,
        )

        for resp in missed:
            # fix 14: replayed private messages must keep their type
            msg = {"type": "narrative", **resp}
            if "private" in resp:
                msg["type"] = "private"
                msg["from"] = "DM"
            await self.send_to_player(player_id, msg)

        if missed:
            logger.info(f"Replayed {len(missed)} messages for {player_id}")
        return len(missed)

    def update_last_seen(self, player_id: str, timestamp: str) -> None:
        """Update the last-seen timestamp for a player."""
        with self._lock:
            self._last_seen[player_id] = timestamp

    def mark_pong(self, player_id: str) -> None:
        """Record a pong response from a player."""
        with self._lock:
            self._last_pong[player_id] = time.time()

    def get_stale_players(self, timeout_seconds: float = 60.0) -> list[str]:
        """
        Get players whose last pong is older than timeout.

        Args:
            timeout_seconds: Staleness threshold

        Returns:
            List of stale player_ids
        """
        now = time.time()
        with self._lock:
            stale = []
            for player_id in self._connections:
                last = self._last_pong.get(player_id, now)
                if now - last > timeout_seconds:
                    stale.append(player_id)
            return stale


class PartyServer:
    """
    Party Mode web server running in a background thread.

    Manages the Starlette app, Uvicorn server, and integration with
    the MCP server's data layer.

    Attributes:
        token_manager: Token authentication manager
        connection_manager: WebSocket connection manager
        pc_registry: Player character registry
        permission_resolver: Permission validation
        storage: Campaign storage manager
        campaign_dir: Path to the active campaign directory
        host: Server bind address
        port: Server port
        start_time: Server start timestamp
    """

    def __init__(
        self,
        pc_registry: PCRegistry,
        permission_resolver: PermissionResolver,
        storage: DnDStorage,
        campaign_dir: Path,
        host: str = "0.0.0.0",
        port: int = 8080,
    ) -> None:
        """
        Initialize the Party Mode server.

        Args:
            pc_registry: Player character registry
            permission_resolver: Permission validation
            storage: Campaign storage manager
            campaign_dir: Path to the active campaign directory
            host: Server bind address (default: 0.0.0.0)
            port: Server port (default: 8080)
        """
        # Per-campaign secret (in party/, which is gitignored) -> unguessable, stable tokens
        self.token_manager = TokenManager(secret_path=campaign_dir / "party" / ".token_secret.json")
        self.connection_manager = ConnectionManager()

        # Web Push: reaches a player even with their tab fully backgrounded or
        # closed (the WebSocket-only "Notify me" button couldn't). Keys and
        # subscriptions persist under party/ (gitignored) across restarts.
        self.push_keys = PushKeys(campaign_dir / "party" / ".vapid_private.pem")
        self.push_store = PushSubscriptionStore(campaign_dir / "party" / ".push_subscriptions.json")
        self.push_sender = PushSender(self.push_keys, self.push_store)
        self.pc_registry = pc_registry
        self.permission_resolver = permission_resolver
        self.storage = storage
        self.campaign_dir = campaign_dir

        # DM presence: last time the DM touched the queue (ISO, UTC)
        self.dm_last_active: Optional[str] = None
        self._presence_sent_at = 0.0
        self.host = host
        self.port = port
        self.start_time = datetime.now()

        # TurnManager reference — set externally when combat starts
        self.turn_manager: Optional["TurnManager"] = None

        # Audio streaming — set via setup_audio() when voice is available
        self._audio_manager: Optional["AudioStreamManager"] = None

        # Event loop reference (set when server thread starts)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Initialize queues
        self.action_queue = ActionQueue(campaign_dir)
        self.response_queue = ResponseQueue(
            campaign_dir,
            on_push=self._on_response_pushed,
        )

        # Live sheet updates: ping clients when any character's data changes.
        self._last_characters_hash = self._characters_hash()
        self._char_callback = self._on_character_event
        register = getattr(storage, "register_character_callback", None)
        if callable(register):
            register(self._char_callback)

        # Detect LAN IP for QR codes
        self.host_ip = detect_host_ip()

        # Build Starlette app
        self.app = self._build_app()

        logger.info(f"PartyServer initialized on {host}:{port}")

    def mark_dm_active(self) -> None:
        """Record DM activity; tell connected players (throttled to one frame per 20s)."""
        from datetime import timezone

        self.dm_last_active = datetime.now(timezone.utc).isoformat()
        now = time.monotonic()
        if now - self._presence_sent_at < 20 or not self._loop or self._loop.is_closed():
            return
        self._presence_sent_at = now
        try:
            asyncio.run_coroutine_threadsafe(
                self.connection_manager.broadcast({"type": "dm_presence", "last_active": self.dm_last_active}),
                self._loop,
            )
        except RuntimeError as exc:
            logger.debug("dm_presence broadcast failed: %s", exc)

    def _characters_hash(self) -> str:
        """Stable hash of every character's data (empty string if none loaded)."""
        try:
            chars = self.storage.list_characters_detailed()
            dumps = []
            for c in chars:
                d = c.model_dump(mode="json")
                d.pop("updated_at", None)  # touched on every update; not content
                dumps.append(d)
            blob = json.dumps(dumps, sort_keys=True, default=str)
            return hashlib.sha1(blob.encode("utf-8")).hexdigest()
        except Exception as exc:  # never let a listener break a save
            logger.debug("characters hash failed: %s", exc)
            return ""

    def _on_character_event(self, action: str, *args) -> None:
        """Storage callback (runs in the saving thread).

        Broadcasts a tiny ``character_changed`` ping when character data actually
        changed. Clients re-fetch through the token-checked /character route, so
        no sheet data travels over the websocket.
        """
        new_hash = self._characters_hash()
        if action == "saved" and new_hash == self._last_characters_hash:
            return
        self._last_characters_hash = new_hash
        if not self._loop or self._loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.connection_manager.broadcast({"type": "character_changed"}),
                self._loop,
            )
        except RuntimeError as exc:
            logger.debug("character_changed broadcast failed: %s", exc)

    def detach(self) -> None:
        """Unhook from storage (called when the server stops)."""
        unregister = getattr(self.storage, "unregister_character_callback", None)
        if callable(unregister):
            unregister(self._char_callback)

    def _on_response_pushed(self, response: dict) -> None:
        """
        Callback fired when a response is pushed to the queue.

        Bridges from the caller's thread to the server's event loop
        using asyncio.run_coroutine_threadsafe.
        """
        if not self._loop or self._loop.is_closed():
            logger.warning("No event loop available for broadcast")
            return

        async def _do_broadcast():
            await self.connection_manager.broadcast_response(
                response, self.permission_resolver
            )
            # Notify action status if this response references an action
            action_id = response.get("action_id")
            if action_id:
                # Find who submitted the action and notify them
                action_status_msg = {
                    "type": "action_status",
                    "action_id": action_id,
                    "status": "resolved",
                }
                await self.connection_manager.broadcast(action_status_msg)
            await self._send_push_for_response(response)

        try:
            asyncio.run_coroutine_threadsafe(_do_broadcast(), self._loop)
        except RuntimeError as e:
            logger.error(f"Failed to schedule broadcast: {e}")

    async def _send_push_for_response(self, response: dict) -> None:
        """Web Push companion to broadcast_response.

        Sent to every player with a stored subscription, regardless of
        live WebSocket state — that's the whole point, since a dropped
        socket (mobile browsers do this aggressively while backgrounded)
        is exactly when a player needs the push, not the live broadcast.
        The service worker itself (static/sw.js) skips showing it only if
        the player has a game tab focused right now; a tab that's merely
        open (or visible on a screen they're not looking at) still gets it.
        notifyNew() in app.js leaves the system notification to this path
        whenever the device is push-subscribed, so the two never double up.
        """
        subscribed = self.push_store.all()
        if not subscribed:
            return
        for player_id in list(subscribed.keys()):
            filtered = bridge.format_response(response, player_id, self.permission_resolver)
            text = (filtered.get("private") or filtered.get("narrative") or "").strip()
            if not text:
                continue
            body = re.sub(r"[*_#>`]", "", text)
            body = re.sub(r"\s+", " ", body).strip()
            if len(body) > 140:
                body = body[:137] + "…"
            payload = {
                "title": "The DM (private)" if "private" in filtered else "The DM",
                "body": body,
                "tag": "dm-reply",
            }
            try:
                await asyncio.to_thread(self.push_sender.send_to, player_id, payload)
            except Exception as exc:  # never let a push failure break the broadcast
                logger.warning(f"Push dispatch failed for {player_id}: {exc}")

    def _build_app(self) -> Starlette:
        """
        Build the Starlette application with routes.

        Returns:
            Configured Starlette app
        """
        static_dir = Path(__file__).parent / "static"
        routes = [
            Route("/", self.get_root, methods=["GET"]),
            Route("/play", self.get_play, methods=["GET"]),
            Route("/map", self.get_map, methods=["GET"]),
            Route("/action", self.post_action, methods=["POST"]),
            Route("/action/{action_id}/status", self.get_action_status, methods=["GET"]),
            Route("/character/{player_id}", self.get_character, methods=["GET"]),
            Route("/status", self.get_status, methods=["GET"]),
            Route("/push/vapid-key", self.get_vapid_public_key, methods=["GET"]),
            Route("/push/subscribe", self.post_push_subscribe, methods=["POST"]),
            Route("/push/unsubscribe", self.post_push_unsubscribe, methods=["POST"]),
            WebSocketRoute("/ws", self.websocket_endpoint),
            Mount("/static", app=StaticFiles(directory=str(static_dir)), name="static"),
        ]

        return Starlette(debug=False, routes=routes)

    async def get_root(self, request: Request) -> Response:
        """Landing page confirming the server is running."""
        connected = self.connection_manager.get_connected_players()
        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>DM20 Party Mode</title>
<style>body{{font-family:system-ui,sans-serif;max-width:600px;margin:60px auto;padding:0 20px;background:#1a1a2e;color:#e0e0e0;text-align:center}}
h1{{color:#e94560;font-size:2em}}p{{color:#a0a0b0;line-height:1.6}}.status{{background:#16213e;padding:20px;border-radius:12px;margin:30px 0}}
.badge{{display:inline-block;background:#0f3460;padding:6px 14px;border-radius:20px;margin:4px;font-size:0.9em}}</style></head>
<body><h1>DM20 Party Mode</h1>
<div class="status"><p>Server is running</p>
<p><strong>{len(connected)}</strong> player(s) connected</p>
{"".join(f'<span class="badge">{p}</span>' for p in connected) if connected else '<p style="color:#666">No players connected yet</p>'}
</div>
<p>Players should use their personal QR code or URL to connect.<br>
This page is for the DM to verify the server is running.</p></body></html>"""
        return HTMLResponse(html)

    async def get_play(self, request: Request) -> Response:
        """
        Serve the player UI HTML page.

        Validates the session token and serves static/index.html with
        the player's name injected.

        Args:
            request: Starlette request object

        Returns:
            HTML response or 401 Unauthorized
        """
        token = request.query_params.get("token")
        if not token:
            return PlainTextResponse("Missing token", status_code=401)

        player_id = self.token_manager.validate_token(token)
        if not player_id:
            return PlainTextResponse("Invalid token", status_code=401)

        # Load static HTML and inject player name
        static_file = Path(__file__).parent / "static" / "index.html"
        if not static_file.exists():
            return PlainTextResponse("UI not available", status_code=503)

        html = static_file.read_text()
        # Resolve display name from storage (character name), fallback to player_id
        display_name = player_id
        if player_id != "OBSERVER":
            try:
                char = self.storage.get_character(player_id)
                if char:
                    display_name = char.name
            except Exception:
                pass  # Fallback to player_id if lookup fails
        html = html.replace("{player_id}", player_id)
        html = html.replace("{player_name}", display_name)
        html = html.replace("{token}", token)
        html = html.replace("{vapid_public_key}", self.push_keys.public_key_b64)
        # A Map button appears only when the campaign has a map page.
        map_link = ""
        if self._map_file().exists():
            map_link = (
                f'<a class="mute-btn" id="map-btn" href="/map?token={token}" '
                'target="_blank" rel="noopener" title="Open the map">🗺️</a>'
            )
        html = html.replace("{map_link}", map_link)
        # Content hash of the page's assets, so a reload after a server
        # update fetches the new app.js/style.css instead of a cached copy.
        html = html.replace("{asset_version}", self._asset_version(static_file.parent))

        return HTMLResponse(html)

    def _map_file(self) -> Path:
        return self.campaign_dir / "maps" / "index.html"

    async def get_map(self, request: Request) -> Response:
        """
        Serve the campaign's player-facing map page (campaign/maps/index.html).

        Token-gated like /play. The map holds only what the party knows; it's
        the same page the DM publishes elsewhere, so a bare HTML fragment
        (no doctype) gets a minimal document wrapper here.

        Args:
            request: Starlette request object

        Returns:
            HTML response, 401 Unauthorized, or 404 if the campaign has no map
        """
        token = request.query_params.get("token")
        if not token:
            return PlainTextResponse("Missing token", status_code=401)
        if not self.token_manager.validate_token(token):
            return PlainTextResponse("Invalid token", status_code=401)

        map_file = self._map_file()
        if not map_file.exists():
            return PlainTextResponse("No map for this campaign yet", status_code=404)

        html = map_file.read_text(encoding="utf-8")
        if not html.lstrip().lower().startswith("<!doctype"):
            html = (
                '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
                "<style>*,*::before,*::after{box-sizing:border-box}body{margin:0}"
                "[hidden]{display:none!important}</style></head><body>\n"
                + html
                + "\n</body></html>"
            )
        return HTMLResponse(html)

    @staticmethod
    def _asset_version(static_dir: Path) -> str:
        digest = hashlib.sha256()
        for name in ("app.js", "style.css"):
            try:
                digest.update((static_dir / name).read_bytes())
            except OSError:
                pass
        return digest.hexdigest()[:12]

    async def post_action(self, request: Request) -> Response:
        """
        Handle player action submission.

        Validates token and queues the action for host processing.

        Args:
            request: Starlette request object

        Returns:
            JSON response with action_id and status
        """
        # Extract token from Authorization header or query param
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            token = request.query_params.get("token", "")

        player_id = self.token_manager.validate_token(token)
        if not player_id:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        # Turn gating: reject if combat is active and not this player's turn
        gate_error = self._check_turn_gate(player_id)
        if gate_error:
            return JSONResponse({"error": gate_error}, status_code=403)

        try:
            body = await request.json()
            action_text = body.get("action", "")
            is_private = bool(body.get("private", False))
        except Exception as e:
            return JSONResponse({"error": f"Invalid request: {e}"}, status_code=400)

        if not action_text.strip():
            return JSONResponse({"error": "Empty action"}, status_code=400)

        action_id = self.action_queue.push(player_id, action_text, private=is_private)
        logger.info(f"Action queued: {action_id} from {player_id} (private={is_private})")

        return JSONResponse({
            "success": True,
            "action_id": action_id,
            "player_id": player_id,
            "status": "pending",
        })

    async def get_action_status(self, request: Request) -> Response:
        """
        Get the status of a submitted action.

        Args:
            request: Starlette request object

        Returns:
            JSON response with action status
        """
        action_id = request.path_params.get("action_id")
        if not action_id:
            return JSONResponse({"error": "Missing action_id"}, status_code=400)

        status = self.action_queue.get_status(action_id)
        if status is None:
            return JSONResponse({"error": "Action not found"}, status_code=404)

        return JSONResponse({
            "action_id": action_id,
            "status": status,
        })

    async def get_vapid_public_key(self, request: Request) -> Response:
        """
        Public VAPID key the client passes to pushManager.subscribe().

        Not secret — it identifies this campaign's push "application server"
        to the browser's push service. Also injected directly into the
        /play page (see get_play), so this route mainly exists as a
        fallback / for re-fetching without a full page reload.
        """
        return JSONResponse({"key": self.push_keys.public_key_b64})

    async def post_push_subscribe(self, request: Request) -> Response:
        """
        Register one device's Web Push subscription for a player (a player
        can have several; re-subscribing the same device just refreshes it).

        Args:
            request: Starlette request object; body is the PushSubscription
                JSON (endpoint + keys.p256dh + keys.auth) from the browser.

        Returns:
            JSON success response, or 401/400 on auth/validation failure.
        """
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            token = request.query_params.get("token", "")

        player_id = self.token_manager.validate_token(token)
        if not player_id:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception as e:
            return JSONResponse({"error": f"Invalid request: {e}"}, status_code=400)

        endpoint = body.get("endpoint")
        keys = body.get("keys") or {}
        if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
            return JSONResponse({"error": "Invalid subscription"}, status_code=400)

        self.push_store.add(player_id, {"endpoint": endpoint, "keys": keys})
        logger.info(f"Push subscription registered for {player_id}")
        return JSONResponse({"success": True})

    async def post_push_unsubscribe(self, request: Request) -> Response:
        """
        Remove a player's Web Push subscription — just the one device named
        by an "endpoint" in the JSON body, or all of the player's devices if
        the body has none.

        Args:
            request: Starlette request object

        Returns:
            JSON success response, or 401 on auth failure.
        """
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            token = request.query_params.get("token", "")

        player_id = self.token_manager.validate_token(token)
        if not player_id:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            body = {}
        endpoint = body.get("endpoint") if isinstance(body, dict) else None
        self.push_store.remove(player_id, endpoint)
        return JSONResponse({"success": True})

    async def get_character(self, request: Request) -> Response:
        """
        Get character data for a specific player.

        Validates token and checks permissions before returning character data.

        Args:
            request: Starlette request object

        Returns:
            JSON response with character data or error
        """
        player_id = request.path_params.get("player_id")
        if not player_id:
            return JSONResponse({"error": "Missing player_id"}, status_code=400)

        # Validate token
        token = request.query_params.get("token", "")
        requesting_player = self.token_manager.validate_token(token)
        if not requesting_player:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        # Check permission
        allowed = self.permission_resolver.check_permission(
            requesting_player,
            "get_character",
            player_id
        )
        if not allowed:
            return JSONResponse(
                {"error": "Permission denied"},
                status_code=403
            )

        # Get character data from storage
        try:
            character = self.storage.get_character(player_id)
            # Use mode='json' to handle datetime and other non-standard types
            return JSONResponse(character.model_dump(mode='json'))
        except Exception as e:
            logger.error(f"Failed to get character {player_id}: {e}")
            return JSONResponse(
                {"error": f"Character not found: {e}"},
                status_code=404
            )

    async def get_status(self, request: Request) -> Response:
        """
        Get server health and status information.

        Returns:
            JSON response with server status
        """
        uptime_seconds = (datetime.now() - self.start_time).total_seconds()
        connected_players = self.connection_manager.get_connected_players()

        return JSONResponse({
            "status": "running",
            "uptime_seconds": uptime_seconds,
            "connected_players": connected_players,
            "total_connections": self.connection_manager.connection_count(),
            "active_pcs": len(self.pc_registry.get_all_active()),
        })

    def broadcast_combat_update(self) -> None:
        """
        Schedule a combat state broadcast from any thread.

        Thread-safe method that bridges to the server's event loop.
        Call this whenever combat state changes (turn advance, combat
        start/end) so all connected players receive an immediate update.
        """
        if not self._loop or self._loop.is_closed():
            logger.warning("No event loop available for combat broadcast")
            return

        if self.turn_manager is None:
            return

        turn_mgr = self.turn_manager
        storage = self.storage
        perm = self.permission_resolver

        async def _do_combat_broadcast() -> None:
            await self.connection_manager.broadcast_combat_state(
                turn_mgr, storage, perm
            )

        try:
            asyncio.run_coroutine_threadsafe(_do_combat_broadcast(), self._loop)
        except RuntimeError as e:
            logger.error(f"Failed to schedule combat broadcast: {e}")

    def setup_audio(
        self,
        tts_router: object,
        voice_registry: object,
        chunk_size: int = 4096,
    ) -> None:
        """
        Attach a VoiceRegistry and TTSRouter to enable audio streaming.

        Call this after the TTS subsystem is initialised and a campaign
        is loaded.  Once set up, ``stream_audio`` can deliver TTS audio
        to connected players over WebSocket.

        Args:
            tts_router: Initialised TTSRouter instance.
            voice_registry: Campaign VoiceRegistry instance.
            chunk_size: Audio chunk size in bytes (default 4096).
        """
        from dm20_protocol.voice.streaming import AudioStreamManager

        self._audio_manager = AudioStreamManager(
            tts_router=tts_router,
            voice_registry=voice_registry,
            connection_manager=self.connection_manager,
            chunk_size=chunk_size,
        )
        logger.info("Audio streaming enabled for Party Mode")

    def stream_audio(
        self,
        text: str,
        *,
        speaker: str = "dm",
        context: str = "default",
        player_id: Optional[str] = None,
    ) -> None:
        """
        Schedule TTS synthesis and audio streaming from any thread.

        Thread-safe method that bridges to the server's event loop.
        If audio is not set up or streaming fails, this is a no-op
        (text delivery still works via the regular response queue).

        Args:
            text: Text to synthesise.
            speaker: Speaker identifier for voice lookup.
            context: Synthesis context for engine selection.
            player_id: Specific player, or None to broadcast to all.
        """
        if self._audio_manager is None:
            return

        if not self._loop or self._loop.is_closed():
            logger.warning("No event loop available for audio streaming")
            return

        mgr = self._audio_manager

        async def _do_stream() -> None:
            if player_id:
                await mgr.stream_to_player(
                    player_id, text, speaker=speaker, context=context
                )
            else:
                await mgr.stream_to_all(text, speaker=speaker, context=context)

        try:
            asyncio.run_coroutine_threadsafe(_do_stream(), self._loop)
        except RuntimeError as e:
            logger.error(f"Failed to schedule audio streaming: {e}")

    def _check_turn_gate(self, player_id: str) -> Optional[str]:
        """
        Check if a player is allowed to submit an action based on
        combat turn gating.

        If combat is not active or no TurnManager is set, actions are
        always allowed (returns None). During turn-based combat, only
        the player whose turn it is may act.

        Args:
            player_id: The player attempting to act

        Returns:
            None if action is allowed, or an error message string if blocked
        """
        if self.turn_manager is None:
            return None

        from dm20_protocol.claudmaster.turn_manager import TurnPhase

        state = self.turn_manager.state
        if state is None or state.phase != TurnPhase.COMBAT:
            return None

        # In simultaneous / free-form mode, everyone can act
        from dm20_protocol.claudmaster.turn_manager import TurnDistribution

        if state.distribution_mode == TurnDistribution.FREE_FORM:
            return None

        # Turn-based mode: only current player may act
        if not bridge.is_players_turn(player_id, self.turn_manager):
            current = self.turn_manager.get_current_turn() or "unknown"
            return f"Not your turn. Waiting for {current}."

        return None

    def _display_name(self, player_id: str) -> str:
        """Resolve a player_id/token to its character name, falling back to the token."""
        if player_id == "OBSERVER":
            return player_id
        try:
            char = self.storage.get_character(player_id)
            if char:
                return char.name
        except Exception:
            pass
        return player_id

    async def websocket_endpoint(self, websocket: WebSocket) -> None:
        """
        Handle WebSocket connections for real-time updates.

        Includes join/leave broadcasting, heartbeat pings, and
        reconnection with message replay.

        Args:
            websocket: WebSocket connection
        """
        # Validate token from query params
        token = websocket.query_params.get("token")
        if not token:
            await websocket.close(code=1008, reason="Missing token")
            return

        player_id = self.token_manager.validate_token(token)
        if not player_id:
            await websocket.close(code=1008, reason="Invalid token")
            return

        # Register connection
        await self.connection_manager.connect(player_id, websocket)
        self.connection_manager.mark_pong(player_id)

        # Broadcast join event to all other players
        await self.connection_manager.broadcast({
            "type": "system",
            "content": f"{self._display_name(player_id)} joined",
            "timestamp": datetime.now().isoformat(),
        })

        # Start heartbeat task for this connection
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(player_id, websocket)
        )

        try:
            # Send initial connection confirmation
            await websocket.send_json({
                "type": "connected",
                "player_id": player_id,
                "timestamp": datetime.now().isoformat(),
                "dm_last_active": self.dm_last_active,
            })

            # Keep connection alive and handle incoming messages
            while True:
                message = await websocket.receive_json()
                await self._handle_ws_message(player_id, message, websocket)

        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected: {player_id}")
        except Exception as e:
            logger.error(f"WebSocket error for {player_id}: {e}")
        finally:
            heartbeat_task.cancel()
            self.connection_manager.disconnect(player_id, websocket)

            # Broadcast leave event
            await self.connection_manager.broadcast({
                "type": "system",
                "content": f"{self._display_name(player_id)} disconnected",
                "timestamp": datetime.now().isoformat(),
            })

    async def _handle_ws_message(
        self, player_id: str, message: dict, websocket: WebSocket
    ) -> None:
        """
        Handle incoming WebSocket messages from a player.

        Args:
            player_id: The player who sent the message
            message: The message dict
            websocket: The WebSocket connection
        """
        msg_type = message.get("type")

        if msg_type == "heartbeat" or msg_type == "pong":
            self.connection_manager.mark_pong(player_id)
            try:
                self.pc_registry.heartbeat(player_id)
            except Exception:
                pass  # PCRegistry may not track this player
        elif msg_type == "action":
            # Turn gating: reject if combat is active and not this player's turn
            gate_error = self._check_turn_gate(player_id)
            if gate_error:
                await self.connection_manager.send_to_player(player_id, {
                    "type": "action_status",
                    "action_id": None,
                    "status": "rejected",
                    "error": gate_error,
                })
                return

            action_text = message.get("text", "")
            if action_text.strip():
                action_id = self.action_queue.push(player_id, action_text)
                await self.connection_manager.send_to_player(player_id, {
                    "type": "action_status",
                    "action_id": action_id,
                    "status": "pending",
                })
        elif msg_type == "history_request":
            since = message.get("since")
            await self.connection_manager.handle_reconnect(
                player_id, since, self.response_queue, self.permission_resolver
            )
        else:
            logger.debug(f"Unknown WebSocket message type: {msg_type}")

    async def _heartbeat_loop(
        self, player_id: str, websocket: WebSocket
    ) -> None:
        """
        Send periodic ping messages to detect stale connections.

        Runs as an asyncio task per WebSocket connection. Sends a ping
        every 30 seconds. If no pong is received within 60 seconds,
        the connection is closed.

        Args:
            player_id: The player to ping
            websocket: The WebSocket connection
        """
        try:
            while True:
                await asyncio.sleep(30)
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break

                # Check for stale connection
                stale = self.connection_manager.get_stale_players(timeout_seconds=60.0)
                if player_id in stale:
                    logger.warning(f"Stale connection detected: {player_id}")
                    await websocket.close(code=1001, reason="Stale connection")
                    break
        except asyncio.CancelledError:
            pass


def _run_server(server: PartyServer, stop_event: threading.Event) -> None:
    """
    Run the Uvicorn server in a background thread.

    This function creates a new asyncio event loop and runs the Uvicorn
    server until stop_event is set.

    Args:
        server: The PartyServer instance
        stop_event: Event to signal server shutdown
    """
    # Create a new event loop for this thread and store it on the server
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    server._loop = loop

    config = uvicorn.Config(
        server.app,
        host=server.host,
        port=server.port,
        log_level="info",
        loop="asyncio",
    )
    uvicorn_server = uvicorn.Server(config)

    async def serve() -> None:
        """Run server until stop_event is set."""
        await uvicorn_server.serve()

    async def shutdown_monitor() -> None:
        """Monitor stop_event and shutdown server when signaled."""
        while not stop_event.is_set():
            await asyncio.sleep(0.1)
        await server.connection_manager.close_all()
        uvicorn_server.should_exit = True

    # Run both tasks
    try:
        loop.run_until_complete(asyncio.gather(
            serve(),
            shutdown_monitor(),
        ))
    except Exception as e:
        logger.error(f"Server thread error: {e}")
    finally:
        loop.close()
        logger.info("Server thread exited")


def start_party_server(
    pc_registry: PCRegistry,
    permission_resolver: PermissionResolver,
    storage: DnDStorage,
    campaign_dir: Path,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> PartyServer:
    """
    Start the Party Mode web server in a background thread.

    This function MUST NOT block. It starts a daemon thread running
    Uvicorn with its own asyncio event loop, separate from the main
    thread (which runs the MCP stdio transport).

    Args:
        pc_registry: Player character registry
        permission_resolver: Permission validation
        storage: Campaign storage manager
        campaign_dir: Path to the active campaign directory
        host: Server bind address (default: 0.0.0.0)
        port: Server port (default: 8080)

    Returns:
        The PartyServer instance

    Raises:
        RuntimeError: If server is already running
    """
    global _server_thread, _server_instance, _stop_event

    if _server_thread is not None and _server_thread.is_alive():
        raise RuntimeError("Party server is already running")

    # Create server instance
    server = PartyServer(
        pc_registry=pc_registry,
        permission_resolver=permission_resolver,
        storage=storage,
        campaign_dir=campaign_dir,
        host=host,
        port=port,
    )

    # Create stop event
    stop_event = threading.Event()

    # Start background thread
    thread = threading.Thread(
        target=_run_server,
        args=(server, stop_event),
        daemon=True,
        name="PartyModeServer",
    )
    thread.start()

    # Store globals
    _server_thread = thread
    _server_instance = server
    _stop_event = stop_event

    # Wait a moment for server to start
    time.sleep(0.5)

    logger.info(f"Party Mode server started on http://{server.host_ip}:{port}")
    return server


def stop_party_server() -> None:
    """
    Stop the Party Mode web server gracefully.

    Closes all WebSocket connections and shuts down the Uvicorn server.
    Waits up to 5 seconds for the server thread to exit.

    Raises:
        RuntimeError: If server is not running
    """
    global _server_thread, _server_instance, _stop_event

    if _server_thread is None or not _server_thread.is_alive():
        raise RuntimeError("Party server is not running")

    logger.info("Stopping Party Mode server...")

    if _server_instance is not None:
        _server_instance.detach()

    # Signal shutdown
    if _stop_event:
        _stop_event.set()

    # Wait for thread to exit
    _server_thread.join(timeout=5.0)

    if _server_thread.is_alive():
        logger.warning("Server thread did not exit cleanly")
    else:
        logger.info("Party Mode server stopped")

    # Clear globals
    _server_thread = None
    _server_instance = None
    _stop_event = None


def get_server_instance() -> Optional[PartyServer]:
    """
    Get the current PartyServer instance if running.

    Returns:
        The PartyServer instance, or None if not running
    """
    return _server_instance


__all__ = [
    "PartyServer",
    "ConnectionManager",
    "start_party_server",
    "stop_party_server",
    "get_server_instance",
]
