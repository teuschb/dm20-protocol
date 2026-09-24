"""Tests for live sheet updates: storage saves ping party clients."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dm20_protocol.models import AbilityScore, Character, CharacterClass, Race
from dm20_protocol.party import server as server_mod
from dm20_protocol.party.server import PartyServer
from dm20_protocol.storage import DnDStorage


def _character() -> Character:
    return Character(
        name="Gandalf",
        player_name="John",
        character_class=CharacterClass(name="Wizard", level=3, hit_dice="1d6"),
        race=Race(name="Human"),
        abilities={"strength": AbilityScore(score=10)},
    )


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as d:
        storage = DnDStorage(data_dir=d)
        storage.create_campaign("Test", "desc")
        storage.add_character(_character())
        srv = PartyServer(
            pc_registry=MagicMock(),
            permission_resolver=MagicMock(),
            storage=storage,
            campaign_dir=Path(d),
            host="127.0.0.1",
            port=8899,
        )
        srv._loop = MagicMock()
        srv._loop.is_closed.return_value = False
        sent: list[dict] = []

        def fake_threadsafe(coro, loop):
            # broadcast() was called with the ping dict; close the coroutine unrun
            coro.close()
            sent.append({"type": "character_changed"})

        with patch.object(server_mod.asyncio, "run_coroutine_threadsafe", fake_threadsafe):
            yield storage, srv, sent


def test_real_character_change_sends_one_ping(env):
    storage, srv, sent = env
    storage.update_character("Gandalf", background="Sage")
    assert len(sent) == 1


def test_non_character_save_sends_no_ping(env):
    storage, srv, sent = env
    storage.update_game_state(notes="only notes changed")
    assert sent == []


def test_repeat_save_without_change_sends_nothing(env):
    storage, srv, sent = env
    storage.update_character("Gandalf", background="Sage")
    sent.clear()
    storage.update_character("Gandalf", background="Sage")
    assert sent == []


def test_detach_stops_pings(env):
    storage, srv, sent = env
    srv.detach()
    storage.update_character("Gandalf", background="Hermit")
    assert sent == []
