"""Secret per-campaign tokens + DM presence."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from dm20_protocol.party.auth import TokenManager
from dm20_protocol.party.server import PartyServer


def test_secret_tokens_are_not_the_player_id_and_are_stable(tmp_path: Path):
    path = tmp_path / "party" / ".token_secret.json"
    t1 = TokenManager(secret_path=path).generate_token("aCrXmSHk")
    assert t1 != "aCrXmSHk" and len(t1) >= 16
    assert TokenManager(secret_path=path).generate_token("OBSERVER") != "OBSERVER"
    # a restart (new manager, same secret file) gives the same URL
    tm2 = TokenManager(secret_path=path)
    assert tm2.generate_token("aCrXmSHk") == t1
    assert tm2.validate_token(t1) == "aCrXmSHk"
    assert tm2.validate_token("aCrXmSHk") is None       # the old guessable token is dead
    assert (path.stat().st_mode & 0o777) == 0o600


def test_refresh_really_rotates_and_persists(tmp_path: Path):
    path = tmp_path / ".token_secret.json"
    tm = TokenManager(secret_path=path)
    old = tm.generate_token("p1")
    new = tm.refresh_token("p1")
    assert new != old and tm.validate_token(old) is None and tm.validate_token(new) == "p1"
    assert TokenManager(secret_path=path).generate_token("p1") == new  # survives restart
    assert json.loads(path.read_text())["epochs"]["p1"] == 1


def test_different_campaign_secrets_give_different_tokens(tmp_path: Path):
    a = TokenManager(secret_path=tmp_path / "a.json").generate_token("p1")
    b = TokenManager(secret_path=tmp_path / "b.json").generate_token("p1")
    assert a != b


def test_legacy_mode_unchanged_without_secret():
    assert TokenManager().generate_token("Gandalf") == "Gandalf"


def test_server_uses_campaign_secret_and_tracks_presence(tmp_path: Path):
    srv = PartyServer(pc_registry=MagicMock(), permission_resolver=MagicMock(), storage=MagicMock(),
                      campaign_dir=tmp_path, host="127.0.0.1", port=8898)
    assert (tmp_path / "party" / ".token_secret.json").exists()
    assert srv.token_manager.generate_token("p1") != "p1"
    assert srv.dm_last_active is None
    srv.mark_dm_active()            # no event loop yet: records time, doesn't broadcast
    assert srv.dm_last_active and srv.dm_last_active.endswith("+00:00")


def test_refresh_tool_resolves_character_names(tmp_path: Path):
    """party_refresh_token('Iona Fletcher') must rotate Iona's real token, not mint one
    for a nonexistent player called 'Iona Fletcher'."""
    from types import SimpleNamespace
    from unittest.mock import patch
    from dm20_protocol import main

    tm = TokenManager(secret_path=tmp_path / "s.json")
    old = tm.generate_token("Cnuhvbwc")
    iona = SimpleNamespace(id="Cnuhvbwc", name="Iona Fletcher")
    storage = MagicMock()
    storage.find_character.side_effect = lambda k: iona if k in ("Iona Fletcher", "Cnuhvbwc") else None
    storage.list_characters_detailed.return_value = [iona]
    server = SimpleNamespace(token_manager=tm, host_ip="127.0.0.1", port=1, campaign_dir=tmp_path,
                             pc_registry=MagicMock())
    fn = getattr(main.party_refresh_token, "fn", main.party_refresh_token)
    with patch.object(main, "storage", storage), \
         patch("dm20_protocol.party.server.get_server_instance", return_value=server):
        out = fn(player_name="Iona Fletcher")
        assert "Cnuhvbwc" in out
        assert tm.validate_token(old) is None                 # the real old link is dead
        assert tm.validate_token("Iona Fletcher") is None
        assert fn(player_name="Nobody").startswith("No character matches")
