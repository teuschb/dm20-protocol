"""
Web Push notification support for Party Mode.

Why this exists: the existing "Notify me" button (see static/app.js
notifyNew()) only ever worked while the player's tab was open, JS running,
and the WebSocket connection still alive — because that's the only path a
DM reply had to reach the page. Mobile browsers aggressively suspend
backgrounded tabs and drop their sockets (often within a minute, well
before a player is actually away long enough to need a notification), so
in practice a notification almost never fired. This module adds real
Web Push: the browser's own push service (FCM/Mozilla/Apple, depending on
vendor) can wake the service worker and show a notification even with the
tab fully closed, using the standard VAPID application-server auth scheme.
No third-party service beyond that browser-vendor push endpoint is
involved — the DM's own server sends directly to it.

Key components:
- PushKeys: this campaign's VAPID keypair (generated once, persisted as a
  PEM file under party/, gitignored like the token secret).
- PushSubscriptionStore: per-player subscriptions (endpoint + keys), also
  persisted so they survive server restarts. A player can have several
  (phone + laptop, or someone else opening their link to test) — keeping
  only the latest silently cut off whichever device subscribed first.
- PushSender: sends a payload to every device of one or all subscribed
  players, dropping any subscription the push service reports as gone
  (404/410).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

logger = logging.getLogger("dm20-protocol.party")


# VAPID "sub" claim: a contact for the push service operator. Apple's service
# rejects non-routable values such as mailto:dm@localhost, so the default is a
# syntactically valid address. Override with DM20_VAPID_SUBJECT (mailto: or https:).
DEFAULT_VAPID_SUBJECT = "mailto:dm20-party@example.com"


def get_vapid_subject() -> str:
    """The VAPID contact claim: DM20_VAPID_SUBJECT if valid, else the default."""
    value = os.environ.get("DM20_VAPID_SUBJECT", "").strip()
    if not value:
        return DEFAULT_VAPID_SUBJECT
    if value.startswith("mailto:") and len(value) > len("mailto:") or value.startswith("https://"):
        return value
    logger.warning("Ignoring DM20_VAPID_SUBJECT=%r: must start with mailto: or https://", value)
    return DEFAULT_VAPID_SUBJECT


# Hosts of the browser vendors' Web Push services. A subscription endpoint is a
# URL the server will POST to, so anything else would be an SSRF vector.
_PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",          # Chrome / Edge / Android (FCM)
    "push.services.mozilla.com",   # Firefox (autopush; also updates.push.services.mozilla.com)
    "push.apple.com",              # Safari / iOS (web.push.apple.com)
    "notify.windows.com",          # Windows WNS (*.notify.windows.com)
)


def validate_push_endpoint(endpoint: object) -> Optional[str]:
    """Return an error message if endpoint isn't an https URL on a known push service, else None."""
    if not isinstance(endpoint, str):
        return "Invalid subscription: endpoint must be a string"
    try:
        parts = urlsplit(endpoint)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return "Invalid subscription: endpoint is not a valid URL"
    if parts.scheme != "https":
        return "Invalid subscription: endpoint must use https://"
    if parts.username or parts.password or port not in (None, 443):
        return "Invalid subscription: endpoint must not include credentials or a custom port"
    if not any(host == s or host.endswith("." + s) for s in _PUSH_HOST_SUFFIXES):
        return "Invalid subscription: endpoint is not a recognised Web Push service"
    return None


class PushKeys:
    """Loads or generates this campaign's VAPID keypair.

    Stored as a PEM file (party/.vapid_private.pem, gitignored like the
    token secret) so existing player subscriptions stay valid across
    server restarts — a new keypair invalidates every subscription.
    """

    def __init__(self, pem_path: Path) -> None:
        pem_path.parent.mkdir(parents=True, exist_ok=True)
        # Vapid.from_file() generates and saves a fresh keypair if the file
        # doesn't exist yet, and loads it (PEM or DER) if it does.
        self.vapid = Vapid.from_file(str(pem_path))
        try:
            pem_path.chmod(0o600)
        except OSError:
            pass
        raw = self.vapid.public_key.public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )
        # Uncompressed EC point, base64url, no padding — what the browser's
        # pushManager.subscribe({applicationServerKey: ...}) needs.
        self.public_key_b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class PushSubscriptionStore:
    """Per-player Web Push subscriptions, persisted at party/.push_subscriptions.json."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._data: dict[str, dict] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("Push subscription file unreadable; starting empty")

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self._path)
        try:
            self._path.chmod(0o600)
        except OSError:
            pass

    MAX_PER_PLAYER = 5

    def _subs(self, player_id: str) -> list[dict]:
        """A player's subscriptions, upgrading the old one-per-player format."""
        subs = self._data.get(player_id)
        if subs is None:
            return []
        if isinstance(subs, dict):  # pre-multi-device file: a single subscription
            subs = [subs]
            self._data[player_id] = subs
        return subs

    def add(self, player_id: str, subscription: dict) -> None:
        """Add (or refresh) one device's subscription; keeps the newest few per player."""
        with self._lock:
            subs = [s for s in self._subs(player_id) if s.get("endpoint") != subscription.get("endpoint")]
            subs.append(subscription)
            self._data[player_id] = subs[-self.MAX_PER_PLAYER:]
            self._save()

    def remove(self, player_id: str, endpoint: Optional[str] = None) -> None:
        """Remove one device's subscription, or all of a player's if no endpoint is given."""
        with self._lock:
            subs = self._subs(player_id)
            if not subs:
                return
            keep = [] if endpoint is None else [s for s in subs if s.get("endpoint") != endpoint]
            if keep:
                self._data[player_id] = keep
            else:
                self._data.pop(player_id, None)
            self._save()

    def all(self) -> dict[str, list[dict]]:
        with self._lock:
            return {pid: list(self._subs(pid)) for pid in list(self._data)}


class PushSender:
    """Sends Web Push notifications; drops subscriptions the push service reports as gone."""

    def __init__(self, keys: PushKeys, store: PushSubscriptionStore) -> None:
        self._keys = keys
        self._store = store

    def send_to(self, player_id: str, payload: dict) -> bool:
        """Send one payload to every device a player has subscribed. Safe to
        call from a worker thread (this makes blocking HTTP requests to the
        push service) — never raises; failures are logged. Returns True if
        at least one device accepted it.
        """
        delivered = False
        for sub in self._store.all().get(player_id, []):
            if self._send_one(player_id, sub, payload):
                delivered = True
        return delivered

    def _send_one(self, player_id: str, sub: dict, payload: dict) -> bool:
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps(payload),
                vapid_private_key=self._keys.vapid,
                vapid_claims={"sub": get_vapid_subject()},
            )
            return True
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                logger.info(f"Push subscription gone for {player_id}; removing that device")
                self._store.remove(player_id, sub.get("endpoint"))
            else:
                logger.warning(f"Push send failed for {player_id}: {exc}")
            return False
        except Exception as exc:  # never let a bad subscription break a broadcast
            logger.warning(f"Push send error for {player_id}: {exc}")
            return False

    def send_to_all(self, payload: dict, exclude: Optional[set[str]] = None) -> int:
        exclude = exclude or set()
        sent = 0
        for player_id in list(self._store.all().keys()):
            if player_id in exclude:
                continue
            if self.send_to(player_id, payload):
                sent += 1
        return sent


__all__ = ["PushKeys", "PushSubscriptionStore", "PushSender", "validate_push_endpoint", "get_vapid_subject"]
