"""update_character can set subclass, spell slots and STRUCTURED features.

These had no tool path before, which forced raw characters.json edits (silently
overwritten by the next tool save) and left features invisible on player sheets.
"""

from dm20_protocol.main import _apply_character_extras, _parse_character_extras
from dm20_protocol.models import AbilityScore, Character, CharacterClass, Feature, Race


def _char() -> Character:
    return Character(
        name="Ovrin", player_name="P",
        character_class=CharacterClass(name="Cleric", level=1, hit_dice="1d8", subclass="Life Domain"),
        race=Race(name="Dwarf"), abilities={"wisdom": AbilityScore(score=19)},
        features=[Feature(name="Dwarven Combat Training", source="Dwarf")],
    )


def _run(char, **kw):
    parsed, err = _parse_character_extras(kw.get("subclass"), kw.get("spell_slots"),
                                          kw.get("add_features"), kw.get("remove_features"))
    assert err is None, err
    return _apply_character_extras(char, parsed)


def test_set_spell_slots_and_clamp_used():
    c = _char()
    c.spell_slots_used = {1: 5, 3: 1}
    _run(c, spell_slots='{"1": 2}')
    assert c.spell_slots == {1: 2}
    assert c.spell_slots_used == {1: 2}          # clamped to new max, level 3 dropped


def test_add_structured_feature_and_update_existing():
    c = _char()
    _run(c, add_features='[{"name": "Improved Critical", "source": "Fighter 3", "description": "19-20", "level_gained": 3}]')
    f = next(x for x in c.features if x.name == "Improved Critical")
    assert (f.source, f.description, f.level_gained) == ("Fighter 3", "19-20", 3)
    _run(c, add_features='[{"name": "improved critical", "description": "crit on 19 or 20"}]')
    assert sum(1 for x in c.features if x.name.lower() == "improved critical") == 1   # deduped by name
    assert next(x for x in c.features if x.name == "Improved Critical").description == "crit on 19 or 20"


def test_string_only_feature_and_remove():
    c = _char()
    _run(c, add_features='["Divine Order: Thaumaturge"]', remove_features='["Dwarven Combat Training"]')
    names = [f.name for f in c.features]
    assert "Divine Order: Thaumaturge" in names and "Dwarven Combat Training" not in names


def test_subclass_set_and_clear():
    c = _char()
    _run(c, subclass="Trickery Domain")
    assert c.classes[0].subclass == "Trickery Domain"
    _run(c, subclass="")
    assert c.classes[0].subclass is None


def test_bad_input_is_rejected_before_any_change():
    for kw in ({"spell_slots": "not json"}, {"spell_slots": '{"12": 1}'}, {"spell_slots": "[1]"},
               {"add_features": "{}"}, {"add_features": '[{"description": "x"}]'}):
        parsed, err = _parse_character_extras(None, kw.get("spell_slots"), kw.get("add_features"), None)
        assert err and parsed == {}
