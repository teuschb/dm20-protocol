"""Tests for limited-use features (Bardic Inspiration, Second Wind, ...)."""

import json

from dm20_protocol.models import Character, CharacterClass, Feature, Race
from dm20_protocol.main import (
    _apply_character_extras,
    _long_rest_logic,
    _parse_character_extras,
    _short_rest_logic,
    _use_feature_logic,
)
from dm20_protocol.sheets.renderer import CharacterSheetRenderer


def make_character(*features: Feature) -> Character:
    return Character(
        name="Lou",
        character_class=CharacterClass(name="Bard", level=3, hit_dice="3d8"),
        race=Race(name="Elf"),
        hit_points_max=21,
        hit_points_current=21,
        hit_dice_type="d8",
        hit_dice_remaining="3d8",
        features=list(features),
    )


def bardic(used: int = 0) -> Feature:
    return Feature(name="Bardic Inspiration", source="Bard 1", uses_max=3, uses_used=used, recharge="long")


def action_surge(used: int = 0) -> Feature:
    return Feature(name="Action Surge", source="Fighter 2", uses_max=1, uses_used=used, recharge="short")


def second_wind(used: int = 0) -> Feature:
    """2024 Second Wind: two uses, one back on a short rest, all on a long rest."""
    return Feature(name="Second Wind", source="Fighter 1", uses_max=2, uses_used=used,
                   recharge="long", short_rest_regain=1)


class TestFeatureModel:
    def test_unlimited_by_default(self):
        f = Feature(name="Darkvision", source="Elf")
        assert f.uses_max is None
        assert f.uses_remaining is None
        assert f.uses_label() == ""

    def test_label(self):
        assert bardic(1).uses_label() == "2/3, long rest"
        assert Feature(name="X", source="Y", uses_max=1).uses_label() == "1/1"

    def test_old_saved_features_still_load(self):
        f = Feature(**{"name": "Trance", "source": "Elf", "description": "", "level_gained": 1})
        assert f.uses_max is None and f.uses_used == 0

    def test_round_trips_through_json(self):
        dumped = bardic(2).model_dump(mode="json")
        assert dumped["uses_max"] == 3 and dumped["uses_used"] == 2 and dumped["recharge"] == "long"
        assert Feature(**dumped).uses_remaining == 1


class TestUseFeature:
    def test_spend_one(self):
        c = make_character(bardic())
        result = _use_feature_logic(c, "bardic inspiration")
        assert result.startswith("✅")
        assert "2/3" in result
        assert c.features[0].uses_used == 1

    def test_cannot_overspend(self):
        c = make_character(bardic(3))
        result = _use_feature_logic(c, "Bardic Inspiration")
        assert result.startswith("❌")
        assert c.features[0].uses_used == 3

    def test_spend_more_than_left(self):
        c = make_character(bardic(2))
        assert _use_feature_logic(c, "Bardic Inspiration", 2).startswith("❌")
        assert c.features[0].uses_used == 2

    def test_negative_restores_and_clamps(self):
        c = make_character(bardic(2))
        result = _use_feature_logic(c, "Bardic Inspiration", -5)
        assert result.startswith("✅") and "regained" in result
        assert c.features[0].uses_used == 0

    def test_zero_reports(self):
        c = make_character(bardic(1))
        result = _use_feature_logic(c, "Bardic Inspiration", 0)
        assert result.startswith("ℹ️") and "2/3" in result

    def test_unknown_feature(self):
        assert _use_feature_logic(make_character(), "Rage").startswith("❌")

    def test_feature_without_limit(self):
        c = make_character(Feature(name="Darkvision", source="Elf"))
        result = _use_feature_logic(c, "Darkvision")
        assert result.startswith("❌") and "uses_max" in result


class TestRests:
    def test_long_rest_restores_everything(self):
        c = make_character(bardic(3), second_wind(2), action_surge(1))
        result = _long_rest_logic(c)
        assert all(f.uses_used == 0 for f in c.features)
        for name in ("Bardic Inspiration", "Second Wind", "Action Surge"):
            assert name in result

    def test_short_rest_restores_short_features_fully(self):
        c = make_character(bardic(3), action_surge(1))
        result = _short_rest_logic(c, 0)
        assert c.features[0].uses_used == 3
        assert c.features[1].uses_used == 0
        assert "restored: Action Surge" in result

    def test_short_rest_gives_back_one_second_wind(self):
        c = make_character(second_wind(2))
        result = _short_rest_logic(c, 0)
        assert c.features[0].uses_used == 1
        assert "Second Wind (+1, 1/2)" in result
        _short_rest_logic(c, 0)
        assert c.features[0].uses_used == 0

    def test_short_rest_regain_never_goes_below_zero(self):
        c = make_character(Feature(name="Channel Divinity", source="Cleric 2", uses_max=3,
                                   uses_used=1, recharge="long", short_rest_regain=2))
        _short_rest_logic(c, 0)
        assert c.features[0].uses_used == 0

    def test_full_feature_not_listed_as_restored(self):
        c = make_character(second_wind(0))
        assert "restored" not in _short_rest_logic(c, 0)

    def test_short_rest_with_hit_dice_still_restores(self):
        c = make_character(action_surge(1))
        c.hit_points_current = 10
        result = _short_rest_logic(c, 1)
        assert c.features[0].uses_used == 0
        assert "restored: Action Surge" in result

    def test_no_recharge_is_left_alone(self):
        c = make_character(Feature(name="Wish-ish", source="X", uses_max=1, uses_used=1))
        _long_rest_logic(c)
        _short_rest_logic(c, 0)
        assert c.features[0].uses_used == 1


class TestUpdateCharacterFeatures:
    def apply(self, c, items):
        parsed, err = _parse_character_extras(None, None, json.dumps(items), None)
        assert err is None, err
        return _apply_character_extras(c, parsed)

    def test_add_limit_to_existing_feature(self):
        c = make_character(Feature(name="Bardic Inspiration", source="Bard 1", description="d6"))
        msgs = self.apply(c, [{"name": "Bardic Inspiration", "uses_max": 3, "recharge": "long", "uses_used": 1}])
        f = c.features[0]
        assert (f.uses_max, f.uses_used, f.recharge) == (3, 1, "long")
        assert f.description == "d6"
        assert "2/3, long rest" in msgs[0]

    def test_update_without_uses_keeps_them(self):
        c = make_character(bardic(1))
        self.apply(c, [{"name": "Bardic Inspiration", "description": "d8 now"}])
        assert c.features[0].uses_used == 1 and c.features[0].uses_max == 3

    def test_lowering_max_clamps_used(self):
        c = make_character(bardic(3))
        self.apply(c, [{"name": "Bardic Inspiration", "uses_max": 2}])
        assert c.features[0].uses_used == 2

    def test_clearing_limit(self):
        c = make_character(bardic(2))
        self.apply(c, [{"name": "Bardic Inspiration", "uses_max": None}])
        assert c.features[0].uses_max is None and c.features[0].uses_used == 0

    def test_bad_recharge_rejected(self):
        _, err = _parse_character_extras(None, None, json.dumps([{"name": "X", "recharge": "dawn"}]), None)
        assert err and "recharge" in err

    def test_set_short_rest_regain(self):
        c = make_character(Feature(name="Second Wind", source="Fighter 1"))
        msgs = self.apply(c, [{"name": "Second Wind", "uses_max": 2, "recharge": "long",
                               "short_rest_regain": 1}])
        assert c.features[0].short_rest_regain == 1
        assert "2/2, 1 back on short rest, all on long rest" in msgs[0]

    def test_zero_short_rest_regain_rejected(self):
        _, err = _parse_character_extras(None, None, json.dumps([{"name": "X", "short_rest_regain": 0}]), None)
        assert err and "short_rest_regain" in err

    def test_negative_max_rejected(self):
        _, err = _parse_character_extras(None, None, json.dumps([{"name": "X", "uses_max": -1}]), None)
        assert err and "uses_max" in err


class TestDisplay:
    def test_sheet_shows_uses(self, tmp_path):
        md = CharacterSheetRenderer(tmp_path).render(make_character(bardic(1)))
        assert "**Bardic Inspiration** [2/3, long rest]" in md
