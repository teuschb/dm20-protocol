"""update_character must not drop the player-name index when player_name isn't passed."""

import tempfile

from dm20_protocol.models import AbilityScore, Character, CharacterClass, Race
from dm20_protocol.storage import DnDStorage


def _storage(d):
    s = DnDStorage(data_dir=d)
    s.create_campaign("Test", "desc")
    s.add_character(Character(
        name="Gandalf", player_name="John",
        character_class=CharacterClass(name="Wizard", level=3, hit_dice="1d6"),
        race=Race(name="Human"), abilities={"strength": AbilityScore(score=10)},
    ))
    return s


def test_unrelated_update_keeps_player_name_lookup():
    with tempfile.TemporaryDirectory() as d:
        s = _storage(d)
        s.update_character("Gandalf", background="Sage")
        assert s.get_character("John").name == "Gandalf"


def test_player_name_change_still_reindexes():
    with tempfile.TemporaryDirectory() as d:
        s = _storage(d)
        s.update_character("Gandalf", player_name="Jane")
        assert s.get_character("Jane").name == "Gandalf"
        assert s._find_character("John") is None


def test_level_kwarg_applies_to_primary_class():
    with tempfile.TemporaryDirectory() as d:
        s = _storage(d)
        s.update_character("Gandalf", level=7)
        assert s.get_character("Gandalf").character_class.level == 7
