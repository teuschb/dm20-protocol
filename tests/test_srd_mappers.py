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


# ---------------------------------------------------------------------------
# SRD 2024 shapes (real API samples, trimmed)
# ---------------------------------------------------------------------------
from dm20_protocol.rulebooks.models import RacialTrait
from dm20_protocol.rulebooks.sources.srd import _paras, _resolve_cache_dir


def _src24(tmp_path):
    return SRDSource(version="2024", cache_dir=tmp_path)


def test_cache_dir_is_versioned_so_2024_never_reads_2014(tmp_path):
    assert _resolve_cache_dir("2024", tmp_path) == tmp_path / "srd_2024"
    assert _resolve_cache_dir("2024", tmp_path / "srd_2024") == tmp_path / "srd_2024"
    assert _resolve_cache_dir("2014", tmp_path) == tmp_path  # legacy flat layout kept
    assert _src24(tmp_path).cache_dir == tmp_path / "srd_2024"


def test_foreign_version_cache_file_is_discarded(tmp_path):
    src = _src24(tmp_path)
    assert src._is_foreign_version({"url": "/api/2014/classes/bard"})
    assert not src._is_foreign_version({"url": "/api/2024/classes/bard"})
    assert not src._is_foreign_version({"index": "x"})  # no url -> can't tell, keep
    assert not src._is_foreign_version([{"url": "/api/2014/x"}])  # lists are not checked


def test_paras_handles_both_api_shapes():
    assert _paras({"desc": ["a", "b"]}) == ["a", "b"]
    assert _paras({"description": "one\n\ntwo"}) == ["one", "two"]
    assert _paras({}) == []


def test_species_has_no_ability_bonuses_and_keeps_trait_text(tmp_path):
    src = _src24(tmp_path)
    data = {
        "index": "dwarf", "name": "Dwarf", "size": "Medium", "speed": 30,
        "traits": [{"index": "stonecunning", "name": "Stonecunning"}],
        "subspecies": [],
    }
    trait = RacialTrait(index="stonecunning", name="Stonecunning", desc=["Tremorsense."])
    race = src._map_species(data, [trait])
    assert race.ability_bonuses == []          # 2024 species grant none (2014 Gnome had +2 INT)
    assert race.traits[0].desc == ["Tremorsense."]
    assert race.source == "srd-2024"


def test_2024_spell_uses_description_string(tmp_path):
    data = {
        "index": "fireball", "name": "Fireball", "level": 3, "casting_time": "Action",
        "school": {"name": "Evocation"}, "components": ["V", "S", "M"],
        "description": "A bright streak.\n\nEach creature makes a save.",
        "higher_level": "The damage increases by 1d6.",
        "damage": {"damage_type": {"name": "Fire"}, "damage_at_slot_level": {"3": "8d6"}},
    }
    sp = _src24(tmp_path)._map_spell(data)
    assert sp.desc == ["A bright streak.", "Each creature makes a save."]
    assert sp.higher_level == ["The damage increases by 1d6."]
    assert sp.damage_at_slot_level == {"3": "8d6"}


def test_2024_feat_prerequisites_dict_and_none(tmp_path):
    src = _src24(tmp_path)
    f = src._map_feat({"index": "grappler", "name": "Grappler", "description": "x",
                       "prerequisites": {"minimum_level": 4, "feature_named": "Fighting Style"}})
    assert {(p.type, p.level, p.feature) for p in f.prerequisites} == {("level", 4, None), ("feature", None, "Fighting Style")}
    assert src._map_feat({"index": "alert", "name": "Alert", "description": "y", "prerequisites": None}).prerequisites == []


def test_2024_equipment_categories_map_to_weapon_fields(tmp_path):
    data = {
        "index": "longsword", "name": "Longsword", "description": [],
        "equipment_categories": [{"index": "martial-melee-weapons"}, {"index": "martial-weapons"},
                                 {"index": "melee-weapons"}, {"index": "weapons"}],
        "cost": {"quantity": 15, "unit": "gp"}, "range": {"normal": 5},
        "damage": {"damage_dice": "1d8"}, "properties": [{"index": "versatile", "name": "Versatile"}],
    }
    it = _src24(tmp_path)._map_item(data)
    assert (it.equipment_category, it.weapon_category, it.weapon_range) == ("weapon", "Martial", "Melee")
    assert it.properties == ["Versatile"]


def test_2024_background_gets_origin_feat(tmp_path):
    data = {"index": "sage", "name": "Sage",
            "ability_scores": [{"name": "CON"}, {"name": "INT"}, {"name": "WIS"}],
            "feat": {"index": "magic-initiate", "name": "Magic Initiate"},
            "proficiencies": [{"name": "Skill: Arcana"}], "equipment_options": []}
    bg = _src24(tmp_path)._map_background(data)
    assert bg.feature.name == "Origin Feat: Magic Initiate"
    assert bg.starting_proficiencies == ["Skill: Arcana"]
