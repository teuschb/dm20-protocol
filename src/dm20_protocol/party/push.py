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
- PushSubscriptionStore: per-player subscription (endpoint + keys), also
  persisted so it survives server restarts.
- PushSender: sends a payload to one or all subscribed players, dropping
  any subscription the push service reports as gone (404/410).
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

logger = logging.getLogger("dm20-protocol.party")


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

    def set(self, player_id: str, subscription: dict) -> None:
        with self._lock:
            self._data[player_id] = subscription
            self._save()

    def remove(self, player_id: str) -> None:
        with self._lock:
            if self._data.pop(player_id, None) is not None:
                self._save()

    def all(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._data)


class PushSender:
    """Sends Web Push notifications; drops subscriptions the push service reports as gone."""

    def __init__(self, keys: PushKeys, store: PushSubscriptionStore) -> None:
        self._keys = keys
        self._store = store

    def send_to(self, player_id: str, payload: dict) -> bool:
        """Send one payload to one player. Safe to call from a worker thread
        (this makes a blocking HTTP request to the push service) — never
        raises; failures are logged and return False.
        """
        sub = self._store.all().get(player_id)
        if not sub:
            return False
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps(payload),
                vapid_private_key=self._keys.vapid,
                vapid_claims={"sub": "mailto:dm@localhost"},
            )
            return True
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                logger.info(f"Push subscription gone for {player_id}; removing")
                self._store.remove(player_id)
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


__all__ = ["PushKeys", "PushSubscriptionStore", "PushSender"]
