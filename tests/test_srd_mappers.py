"""Regression tests for SRD API schema drift and the Kokoro language map."""

from pathlib import Path

from dm20_protocol.rulebooks.sources.srd import SRDSource
from dm20_protocol.voice.engines.kokoro import _LANGUAGE_MAP, KokoroEngine


def _source(tmp_path: Path) -> SRDSource:
    return SRDSource(cache_dir=tmp_path / "cache")


def test_spell_damage_as_list_maps(tmp_path):
    """The live API returns `damage` as a list on some spells (e.g. fireball)."""
    data = {
        "index": "fireball", "name": "Fireball", "level": 3,
        "school": {"name": "Evocation"}, "desc": ["boom"],
        "damage": [{"damage_type": {"index": "fire", "name": "Fire"}}],
    }
    assert _source(tmp_path)._map_spell(data).damage_type == "Fire"


def test_spell_without_damage_maps(tmp_path):
    data = {"index": "light", "name": "Light", "school": {"name": "Evocation"}}
    assert _source(tmp_path)._map_spell(data).damage_type is None


def test_monster_numeric_senses_and_speed_are_stringified(tmp_path):
    """passive_perception is an int and speed.hover a bool in the live API."""
    data = {
        "index": "ghost", "name": "Ghost", "size": "Medium", "type": "undead",
        "armor_class": [{"type": "natural", "value": 11}], "hit_points": 45,
        "senses": {"darkvision": "60 ft.", "passive_perception": 11},
        "speed": {"walk": "0 ft.", "fly": "40 ft.", "hover": True},
    }
    m = _source(tmp_path)._map_monster(data)
    assert m.senses["passive_perception"] == "11"
    assert m.speed["hover"] == "True"


def test_kokoro_maps_english_to_real_lang_codes_not_spanish():
    # "en-us"[0] == "e" is Kokoro's Spanish pipeline; must be "a" / "b"
    assert _LANGUAGE_MAP["en"] == "a"
    assert _LANGUAGE_MAP["en-us"] == "a"
    assert _LANGUAGE_MAP["en-gb"] == "b"
    assert "en" in KokoroEngine().supported_languages()
