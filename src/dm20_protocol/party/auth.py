"""
Authentication system for Party Mode web server.

Handles token generation, validation, and QR code creation for player sessions.
Each player gets a unique session token that encodes their player_id and
provides access to their character's UI.

Key components:
- Token generation from PCRegistry (one token per active PC + OBSERVER token)
- Token validation middleware for protecting routes
- QR code PNG generation for easy mobile access
- Token refresh mechanism to invalidate old tokens
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import socket
import threading
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Optional

import qrcode
from qrcode.image.pil import PilImage

logger = logging.getLogger("dm20-protocol.party")


class TokenManager:
    """
    Manages session tokens for Party Mode authentication.

    Tokens are short, URL-safe strings that map to player_id values.
    Each token is valid for the duration of the server session unless
    explicitly refreshed.

    Attributes:
        _tokens: Dict mapping token -> player_id
        _reverse_index: Dict mapping player_id -> token for O(1) refresh
        _created_at: Dict mapping token -> creation timestamp
    """

    def __init__(self, secret_path: Optional[Path] = None) -> None:
        """Initialize an empty TokenManager.

        Args:
            secret_path: Optional JSON file holding a per-campaign secret and
                per-player rotation counters. When given, tokens are an HMAC of
                the player_id (stable across restarts, unguessable, never equal
                to a value stored in the campaign files). When omitted, the legacy
                behaviour applies: token == player_id ("OBSERVER" for observers).
        """
        self._tokens: dict[str, str] = {}
        self._reverse_index: dict[str, str] = {}
        self._created_at: dict[str, datetime] = {}
        self._secret_path = secret_path
        self._lock = threading.RLock()  # refresh/generate/save may run from several threads
        self._secret: Optional[bytes] = None
        self._epochs: dict[str, int] = {}
        if secret_path is not None:
            self._load_or_create_secret()

    def _load_or_create_secret(self) -> None:
        path = self._secret_path
        assert path is not None
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("Token secret file unreadable; generating a new one (old links stop working)")
                data = {}
        if not data.get("secret"):
            data = {"secret": secrets.token_hex(32), "epochs": {}}
        self._secret = bytes.fromhex(data["secret"])
        self._epochs = {str(k): int(v) for k, v in (data.get("epochs") or {}).items()}
        self._save_secret()

    def _save_secret(self) -> None:
        path = self._secret_path
        if path is None or self._secret is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"secret": self._secret.hex(), "epochs": self._epochs}, indent=2), encoding="utf-8")
        tmp.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _derive_token(self, player_id: str) -> str:
        if self._secret is None:
            return player_id  # legacy deterministic token
        msg = f"{player_id}:{self._epochs.get(player_id, 0)}".encode("utf-8")
        digest = hmac.new(self._secret, msg, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:16]

    def generate_token(self, player_id: str) -> str:
        """
        Generate a deterministic session token for a player.

        The token equals the player_id itself, ensuring stable URLs across
        server restarts. OBSERVER always gets "OBSERVER" as its token.

        If a token already exists for this player_id, the old mapping is
        cleaned up before re-registering.

        Args:
            player_id: Unique identifier for the player (character_id or "OBSERVER")

        Returns:
            The deterministic token string (equal to player_id)
        """
        with self._lock:
            return self._generate_token_locked(player_id)

    def _generate_token_locked(self, player_id: str) -> str:
        # Invalidate existing token if present
        if player_id in self._reverse_index:
            old_token = self._reverse_index[player_id]
            self._tokens.pop(old_token, None)
            self._created_at.pop(old_token, None)

        # Stable across restarts: HMAC(secret, player_id:epoch) when a secret is
        # configured, else the legacy token == player_id.
        token = self._derive_token(player_id)

        # OLD random token generation (preserved for reference):
        # token = secrets.token_urlsafe(6)  # ~8 chars

        # Store mapping
        self._tokens[token] = player_id
        self._reverse_index[player_id] = token
        self._created_at[token] = datetime.now()

        logger.info(f"Generated stable token for player_id={player_id}")
        return token

    def validate_token(self, token: str) -> Optional[str]:
        """
        Validate a session token and return the associated player_id.

        Args:
            token: The token to validate

        Returns:
            The player_id if token is valid, None otherwise
        """
        player_id = self._tokens.get(token)
        if player_id:
            logger.debug(f"Token validated for player_id={player_id}")
        else:
            logger.warning(f"Invalid token presented: {token[:4]}...")
        return player_id

    def refresh_token(self, player_id: str) -> str:
        """
        Invalidate the old token for a player and generate a new one.

        This is useful when a player needs to be re-authenticated
        (e.g., security concern, session timeout).

        Args:
            player_id: The player to refresh the token for

        Returns:
            The new token
        """
        logger.info(f"Refreshing token for player_id={player_id}")
        with self._lock:
            if self._secret is not None:
                # bump this player's epoch so the old link stops working (persisted)
                self._epochs[player_id] = self._epochs.get(player_id, 0) + 1
                self._save_secret()
            return self.generate_token(player_id)

    def revoke_token(self, player_id: str) -> bool:
        """
        Revoke the token for a specific player.

        Args:
            player_id: The player whose token should be revoked

        Returns:
            True if a token was revoked, False if no token existed
        """
        if player_id not in self._reverse_index:
            return False

        token = self._reverse_index[player_id]
        del self._tokens[token]
        del self._reverse_index[player_id]
        del self._created_at[token]

        logger.info(f"Revoked token for player_id={player_id}")
        return True

    def get_all_tokens(self) -> dict[str, str]:
        """
        Get all active tokens.

        Returns:
            Dict mapping player_id -> token
        """
        return dict(self._reverse_index)

    def clear(self) -> None:
        """Clear all tokens."""
        count = len(self._tokens)
        self._tokens.clear()
        self._reverse_index.clear()
        self._created_at.clear()
        logger.info(f"Cleared {count} tokens")


class QRCodeGenerator:
    """
    Generates QR code PNGs and terminal-rendered ASCII art for Party Mode tokens.

    QR codes encode the full URL (http://host:port/play?token=xxx) for
    easy mobile access. Players can scan the code to join the session.
    Terminal rendering uses Unicode block characters for in-terminal display.
    """

    @staticmethod
    def render_qr_terminal(url: str, label: str) -> str:
        """
        Render a QR code as ASCII/Unicode art for terminal display.

        Uses Unicode block characters (half-blocks) for compact rendering
        that works in standard terminal emulators (iTerm2, Terminal.app).

        Args:
            url: The full URL to encode in the QR code
            label: Human-readable label (e.g., character name) displayed above the QR

        Returns:
            A string containing the labeled QR code in Unicode art, ready for print().
            Returns a URL-only fallback string if rendering fails.
        """
        try:
            qr = qrcode.QRCode(
                border=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
            )
            qr.add_data(url)
            qr.make(fit=True)

            # Capture ASCII output to string
            from io import StringIO
            buffer = StringIO()
            qr.print_ascii(out=buffer)
            ascii_art = buffer.getvalue()

            separator = "-" * 50
            return f"\n{separator}\n  {label}\n{separator}\n{ascii_art}  {url}\n{separator}"
        except Exception as e:
            logger.warning(f"Failed to render QR terminal art for {label}: {e}")
            return f"\n  {label}: {url}\n  (QR terminal rendering failed)"

    @staticmethod
    def generate_qr_code(
        url: str,
        output_path: Optional[Path] = None
    ) -> bytes:
        """
        Generate a QR code PNG for the given URL.

        Args:
            url: The full URL to encode in the QR code
            output_path: Optional path to save the PNG file

        Returns:
            PNG image data as bytes
        """
        qr = qrcode.QRCode(
            version=1,  # Auto-size
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        # Save to file if path provided
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(output_path))
            logger.info(f"QR code saved to {output_path}")

        # Return PNG bytes
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def generate_player_qr(
        player_id: str,
        token: str,
        host: str,
        port: int,
        campaign_dir: Path,
        *,
        player_name: str = "",
        character_name: str = "",
    ) -> Path:
        """
        Generate a QR code for a specific player's session.

        When both player_name and character_name are provided, the file is saved as
        "QR {player_name}-{character_name}.png". Otherwise falls back to
        "qr-{player_id}.png". Both cases save under {campaign_dir}/party/.

        The QR encodes the URL: http://{host}:{port}/play?token={token}

        Args:
            player_id: The player's identifier (used as fallback filename)
            token: The session token
            host: Server host IP address
            port: Server port
            campaign_dir: Campaign directory for saving QR codes
            player_name: Optional player name for the filename
            character_name: Optional character name for the filename

        Returns:
            Path to the generated QR code PNG
        """
        url = f"http://{host}:{port}/play?token={token}"

        if player_name and character_name:
            # Sanitize names for filesystem safety
            safe_player = "".join(c for c in player_name if c.isalnum() or c in " _-")
            safe_char = "".join(c for c in character_name if c.isalnum() or c in " _-")
            filename = f"QR {safe_player}-{safe_char}.png"
        else:
            filename = f"qr-{player_id}.png"
        output_path = campaign_dir / "party" / filename

        QRCodeGenerator.generate_qr_code(url, output_path)
        return output_path


def detect_host_ip() -> str:
    """
    Detect the host's LAN IP address for QR code generation.

    Enumerates network interfaces and prefers RFC 1918 private IPs
    (192.168.x.x, 10.x.x.x, 172.16-31.x.x) over VPN/CGNAT IPs
    (e.g. Tailscale 100.x.x.x). Falls back to 127.0.0.1 if no LAN IP found.

    Returns:
        The host IP address as a string
    """
    import ipaddress
    import re
    import subprocess

    rfc1918 = [
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
    ]

    def is_lan_ip(ip_str: str) -> bool:
        """True for RFC 1918 private IPs (real LAN), excludes VPN/CGNAT."""
        try:
            ip = ipaddress.ip_address(ip_str)
            return any(ip in net for net in rfc1918)
        except ValueError:
            return False

    candidates: list[str] = []

    # Method 1: Parse ifconfig output for all interface IPs (macOS/Linux)
    try:
        result = subprocess.run(
            ["ifconfig"], capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0:
            for match in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)", result.stdout):
                ip = match.group(1)
                if not ip.startswith("127."):
                    candidates.append(ip)
    except Exception:
        pass

    # Method 2: UDP connect trick (fallback)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            candidates.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass

    # Method 3: Hostname resolution (last resort)
    try:
        hostname = socket.gethostname()
        addrs = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for addr in addrs:
            ip = addr[4][0]
            if not ip.startswith("127."):
                candidates.append(ip)
    except Exception:
        pass

    # Prefer real LAN IPs: 192.168.x.x > 10.x.x.x > 172.16-31.x.x
    lan_ips = [ip for ip in candidates if is_lan_ip(ip)]

    for prefix in ("192.168.", "10.", "172."):
        for ip in lan_ips:
            if ip.startswith(prefix):
                logger.info(f"Detected host LAN IP: {ip}")
                return ip

    # Any non-localhost IP as fallback (may be VPN)
    non_local = [ip for ip in candidates if not ip.startswith("127.")]
    if non_local:
        logger.warning(f"No LAN IP found, using: {non_local[0]} (may be VPN)")
        return non_local[0]

    logger.warning("Failed to detect host IP, falling back to 127.0.0.1")
    return "127.0.0.1"


__all__ = [
    "TokenManager",
    "QRCodeGenerator",
    "detect_host_ip",
]
