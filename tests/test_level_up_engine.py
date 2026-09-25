"""Tests for LevelUpEngine — character progression."""

import pytest
from unittest.mock import MagicMock, patch

from dm20_protocol.models import (
    AbilityScore,
    Character,
    CharacterClass,
    Feature,
    Race,
)
from dm20_protocol.level_up_engine import (
    LevelUpEngine,
    LevelUpError,
    LevelUpResult,
    STANDARD_ASI_LEVELS,
    FIGHTER_EXTRA_ASI_LEVELS,
    ROGUE_EXTRA_ASI_LEVEL,
    MAX_ABILITY_SCORE,
)


# ─── Mock Helpers ──────────────────────────────────────────────────────


def make_class_level_info(level: int, features: list[str] | None = None, details: dict | None = None):
    """Create a mock ClassLevelInfo."""
    info = MagicMock()
    info.level = level
    info.proficiency_bonus = 2 + (level - 1) // 4
    info.features = features or []
    info.feature_details = details or {}
    return info


def make_fighter_def():
    """Create a mock Fighter ClassDefinition."""
    cls = MagicMock()
    cls.name = "Fighter"
    cls.hit_die = 10
    cls.subclass_level = 3
    cls.subclasses = ["champion", "battle-master", "eldritch-knight"]
    cls.spellcasting = None
    cls.saving_throws = ["STR", "CON"]
    cls.proficiencies = ["All armor", "Shields", "Simple weapons", "Martial weapons"]

    # Set up class levels with features
    cls.class_levels = {
        1: make_class_level_info(1, ["Fighting Style", "Second Wind"]),
        2: make_class_level_info(2, ["Action Surge"]),
        3: make_class_level_info(3, ["Martial Archetype"]),
        4: make_class_level_info(4, ["Ability Score Improvement"]),
        5: make_class_level_info(5, ["Extra Attack"]),
    }
    return cls


def make_wizard_def():
    """Create a mock Wizard ClassDefinition with spellcasting."""
    cls = MagicMock()
    cls.name = "Wizard"
    cls.hit_die = 6
    cls.subclass_level = 2
    cls.subclasses = ["evocation", "abjuration"]
    cls.saving_throws = ["INT", "WIS"]
    cls.proficiencies = ["Daggers", "Darts", "Slings", "Quarterstaffs", "Light crossbows"]

    # Spellcasting
    spell = MagicMock()
    spell.spellcasting_ability = "INT"
    spell.caster_type = "full"
    spell.spell_slots = {
        1: [2],
        2: [3],
        3: [4, 2],
        4: [4, 3],
        5: [4, 3, 2],
    }
    cls.spellcasting = spell

    cls.class_levels = {
        1: make_class_level_info(1, ["Arcane Recovery", "Spellcasting"]),
        2: make_class_level_info(2, ["Arcane Tradition"]),
        3: make_class_level_info(3, []),
        4: make_class_level_info(4, ["Ability Score Improvement"]),
        5: make_class_level_info(5, []),
    }
    return cls


def make_ranger_def():
    """Create a mock Ranger ClassDefinition (half-caster)."""
    cls = MagicMock()
    cls.name = "Ranger"
    cls.hit_die = 10
    cls.subclass_level = 3
    cls.subclasses = ["hunter", "beast-master"]
    cls.saving_throws = ["STR", "DEX"]
    cls.proficiencies = ["Light armor", "Medium armor", "Shields", "Simple weapons", "Martial weapons"]

    # Half-caster spellcasting (starts at level 2)
    spell = MagicMock()
    spell.spellcasting_ability = "WIS"
    spell.caster_type = "half"
    spell.spell_slots = {
        2: [2],
        3: [3],
        4: [3],
        5: [4, 2],
    }
    cls.spellcasting = spell

    cls.class_levels = {
        1: make_class_level_info(1, ["Favored Enemy", "Natural Explorer"]),
        2: make_class_level_info(2, ["Fighting Style", "Spellcasting"]),
        3: make_class_level_info(3, ["Ranger Archetype", "Primeval Awareness"]),
        4: make_class_level_info(4, ["Ability Score Improvement"]),
        5: make_class_level_info(5, ["Extra Attack"]),
    }
    return cls


def make_mock_manager(class_def=None):
    """Create a mock RulebookManager that returns the given class def."""
    manager = MagicMock()
    manager.sources = ["srd"]
    manager.get_class.return_value = class_def
    return manager


def make_character(
    name: str = "Testchar",
    class_name: str = "Fighter",
    level: int = 1,
    con: int = 14,
    hp: int = 12,
) -> Character:
    """Create a test character at the given level."""
    return Character(
        name=name,
        character_class=CharacterClass(
            name=class_name,
            level=level,
            hit_dice=f"{level}d10",
        ),
        race=Race(name="Human"),
        abilities={
            "strength": AbilityScore(score=16),
            "dexterity": AbilityScore(score=12),
            "constitution": AbilityScore(score=con),
            "intelligence": AbilityScore(score=10),
            "wisdom": AbilityScore(score=13),
            "charisma": AbilityScore(score=8),
        },
        hit_points_max=hp,
        hit_points_current=hp,
        hit_dice_type="d10",
        hit_dice_remaining=f"{level}d10",
    )


# ─── Test: LevelUpResult model ────────────────────────────────────────


class TestLevelUpResult:
    def test_create_result(self):
        result = LevelUpResult(
            new_level=2,
            hp_gained=8,
            features_added=["Action Surge"],
            spell_slots_changed=False,
            proficiency_bonus_changed=False,
            summary="Test",
        )
        assert result.new_level == 2
        assert result.hp_gained == 8
        assert result.features_added == ["Action Surge"]
        assert result.asi_applied is None
        assert result.subclass_set is None


# ─── Test: HP Calculation ──────────────────────────────────────────────


class TestHPCalculation:
    """Test HP increase methods."""

    def test_average_d10_positive_con(self):
        """Fighter d10, CON 14 (+2): average = 5+1+2 = 8."""
        char = make_character(con=14, hp=12)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert result.hp_gained == 8  # d10//2 + 1 + 2
        assert char.hit_points_max == 20  # 12 + 8

    def test_average_d6_zero_con(self):
        """Wizard d6, CON 10 (+0): average = 3+1+0 = 4."""
        char = make_character(class_name="Wizard", con=10, hp=6, level=1)
        char.hit_dice_type = "d6"
        char.character_class.hit_dice = "1d6"
        engine = LevelUpEngine(make_mock_manager(make_wizard_def()))
        result = engine.level_up(char)
        assert result.hp_gained == 4  # d6//2 + 1 + 0

    def test_average_negative_con_minimum_1(self):
        """With very low CON, minimum HP gain is 1."""
        char = make_character(con=3, hp=5)  # CON mod = -4
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        # d10//2 + 1 + (-4) = 6 - 4 = 2, still > 1
        assert result.hp_gained == 2

    def test_average_d6_very_negative_con(self):
        """d6 with CON 1 (-5): 3+1+(-5) = -1, minimum 1."""
        char = make_character(class_name="Wizard", con=1, hp=1, level=1)
        char.hit_dice_type = "d6"
        char.character_class.hit_dice = "1d6"
        engine = LevelUpEngine(make_mock_manager(make_wizard_def()))
        result = engine.level_up(char)
        assert result.hp_gained == 1  # minimum

    def test_roll_method(self):
        """Roll method: random 1-die + CON mod, minimum 1."""
        char = make_character(con=14, hp=12)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        with patch("dm20_protocol.level_up_engine.random.randint", return_value=7):
            result = engine.level_up(char, hp_method="roll")
        assert result.hp_gained == 9  # 7 + 2

    def test_roll_method_minimum_1(self):
        """Roll method with low roll and negative CON still gives minimum 1."""
        char = make_character(con=3, hp=5)  # CON mod = -4
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        with patch("dm20_protocol.level_up_engine.random.randint", return_value=1):
            result = engine.level_up(char, hp_method="roll")
        assert result.hp_gained == 1  # max(1 + (-4), 1)

    def test_invalid_hp_method(self):
        char = make_character()
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        with pytest.raises(LevelUpError, match="Unknown hp_method"):
            engine.level_up(char, hp_method="invalid")

    def test_dwarven_toughness_adds_one_hp_per_level(self):
        """Dwarven Toughness (race trait) adds +1 on top of die + CON."""
        char = make_character(con=16, hp=12)  # CON mod +3, avg d10 = 6
        char.race.traits.append("Dwarven Toughness (+1 max HP now, +1 again every level)")
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert result.hp_gained == 10  # 6 + 3 + 1

    def test_tough_feat_adds_two_hp_per_level(self):
        char = make_character(con=14, hp=12)  # CON mod +2, avg d10 = 6
        char.features.append(
            Feature(name="Tough", source="Feat", description="", level_gained=1)
        )
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert result.hp_gained == 10  # 6 + 2 + 2

    def test_flat_hp_bonus_counted_once_across_lists(self):
        """A bonus recorded in traits, features and the legacy list counts once."""
        char = make_character(con=10, hp=12)  # CON mod +0, avg d10 = 6
        char.race.traits.append("Dwarven Toughness")
        char.features.append(
            Feature(name="Dwarven Toughness", source="Dwarf", description="", level_gained=1)
        )
        char.features_and_traits.append("Dwarven Toughness (+1 max HP)")
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert result.hp_gained == 7  # 6 + 0 + 1

    def test_no_flat_bonus_without_trait(self):
        char = make_character(con=10, hp=12)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert result.hp_gained == 6

    def test_hp_applies_to_both_max_and_current(self):
        char = make_character(con=14, hp=12)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert char.hit_points_max == 20
        assert char.hit_points_current == 20


# ─── Test: Level Increment & Hit Dice ─────────────────────────────────


class TestLevelIncrement:
    def test_level_increments(self):
        char = make_character(level=1)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert char.character_class.level == 2
        assert result.new_level == 2

    def test_hit_dice_updated(self):
        char = make_character(level=1)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        engine.level_up(char)
        assert char.hit_dice_remaining == "2d10"
        assert char.character_class.hit_dice == "2d10"

    def test_max_level_error(self):
        char = make_character(level=20, hp=200)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        with pytest.raises(LevelUpError, match="maximum level"):
            engine.level_up(char)


# ─── Test: Features ───────────────────────────────────────────────────


class TestFeatures:
    def test_features_added_at_level(self):
        """Level 1→2 Fighter gains Action Surge."""
        char = make_character(level=1)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert "Action Surge" in result.features_added
        assert any(f.name == "Action Surge" for f in char.features)

    def test_features_have_correct_source(self):
        char = make_character(level=1)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        engine.level_up(char)
        action_surge = next(f for f in char.features if f.name == "Action Surge")
        assert action_surge.source == "Fighter 2"
        assert action_surge.level_gained == 2

    def test_features_also_in_legacy_list(self):
        char = make_character(level=1)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        engine.level_up(char)
        assert "Action Surge" in char.features_and_traits

    def test_no_features_at_some_levels(self):
        """Some levels have no features (empty list)."""
        fighter_def = make_fighter_def()
        fighter_def.class_levels[2] = make_class_level_info(2, [])
        char = make_character(level=1)
        engine = LevelUpEngine(make_mock_manager(fighter_def))
        result = engine.level_up(char)
        assert result.features_added == []

    def test_multiple_features_at_one_level(self):
        """Level 2→3 Fighter gets Martial Archetype."""
        char = make_character(level=2, hp=20)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert "Martial Archetype" in result.features_added


# ─── Test: Subclass ───────────────────────────────────────────────────


class TestSubclass:
    def test_subclass_set_at_correct_level(self):
        """Fighter subclass at level 3."""
        char = make_character(level=2, hp=20)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char, subclass="Champion")
        assert result.subclass_set == "Champion"
        assert char.character_class.subclass == "Champion"

    def test_subclass_invalid_name(self):
        char = make_character(level=2, hp=20)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        with pytest.raises(LevelUpError, match="Invalid subclass"):
            engine.level_up(char, subclass="Nonexistent")

    def test_subclass_note_when_not_provided(self):
        """If no subclass provided at subclass level, note is added to summary."""
        char = make_character(level=2, hp=20)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert "subclass selection level" in result.summary

    def test_subclass_not_asked_at_wrong_level(self):
        """Subclass not relevant at levels other than subclass_level."""
        char = make_character(level=1, hp=12)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char, subclass="Champion")
        # Subclass param is ignored at non-subclass levels
        assert result.subclass_set is None

    def test_wizard_subclass_at_level_2(self):
        """Wizard subclass at level 2 (earlier than most classes)."""
        char = make_character(class_name="Wizard", level=1, con=10, hp=6)
        char.hit_dice_type = "d6"
        char.character_class.hit_dice = "1d6"
        engine = LevelUpEngine(make_mock_manager(make_wizard_def()))
        result = engine.level_up(char, subclass="Evocation")
        assert result.subclass_set == "Evocation"


# ─── Test: ASI ────────────────────────────────────────────────────────


class TestASI:
    def test_asi_at_level_4(self):
        """Standard ASI at level 4: +2 to one ability."""
        char = make_character(level=3, hp=30)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char, asi_choices={"strength": 2})
        assert result.asi_applied == {"strength": 2}
        assert char.abilities["strength"].score == 18  # was 16

    def test_asi_split_bonus(self):
        """ASI split: +1 to two abilities."""
        char = make_character(level=3, hp=30)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(
            char, asi_choices={"strength": 1, "dexterity": 1}
        )
        assert result.asi_applied == {"strength": 1, "dexterity": 1}
        assert char.abilities["strength"].score == 17
        assert char.abilities["dexterity"].score == 13

    def test_asi_total_not_2(self):
        """Total ASI bonus must be exactly 2."""
        char = make_character(level=3, hp=30)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        # Bonus of 3 is invalid per-ability (must be 1 or 2)
        with pytest.raises(LevelUpError, match="must be 1 or 2"):
            engine.level_up(char, asi_choices={"strength": 3})

    def test_asi_total_exceeds_2_with_valid_individual(self):
        """Two abilities with +2 each = total 4, should fail."""
        char = make_character(level=3, hp=30)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        with pytest.raises(LevelUpError, match="total must be exactly 2"):
            engine.level_up(char, asi_choices={"strength": 2, "dexterity": 2})

    def test_asi_invalid_ability(self):
        char = make_character(level=3, hp=30)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        with pytest.raises(LevelUpError, match="Unknown ability"):
            engine.level_up(char, asi_choices={"athletics": 2})

    def test_asi_capped_at_20(self):
        """ASI can't push ability above 20."""
        char = make_character(level=3, hp=30)
        char.abilities["strength"] = AbilityScore(score=19)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char, asi_choices={"strength": 2})
        assert char.abilities["strength"].score == 20
        assert result.asi_applied == {"strength": 1}  # only 1 actually applied

    def test_asi_note_when_not_provided(self):
        """If no ASI choices at ASI level, note is added."""
        char = make_character(level=3, hp=30)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert "Ability Score Improvement" in result.summary

    def test_asi_abbreviation_accepted(self):
        """Accept uppercase abbreviations like STR, DEX."""
        char = make_character(level=3, hp=30)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char, asi_choices={"STR": 2})
        assert char.abilities["strength"].score == 18

    def test_asi_invalid_bonus_value(self):
        char = make_character(level=3, hp=30)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        with pytest.raises(LevelUpError, match="must be 1 or 2"):
            engine.level_up(char, asi_choices={"strength": 0, "dexterity": 2})


class TestASILevels:
    """Test ASI level detection for different classes."""

    @pytest.mark.parametrize("level", sorted(STANDARD_ASI_LEVELS))
    def test_standard_asi_levels(self, level):
        assert LevelUpEngine._is_asi_level(level, "Fighter")
        assert LevelUpEngine._is_asi_level(level, "Wizard")
        assert LevelUpEngine._is_asi_level(level, "Ranger")

    @pytest.mark.parametrize("level", sorted(FIGHTER_EXTRA_ASI_LEVELS))
    def test_fighter_extra_asi(self, level):
        assert LevelUpEngine._is_asi_level(level, "Fighter")
        assert not LevelUpEngine._is_asi_level(level, "Wizard")

    @pytest.mark.parametrize("level", sorted(ROGUE_EXTRA_ASI_LEVEL))
    def test_rogue_extra_asi(self, level):
        assert LevelUpEngine._is_asi_level(level, "Rogue")
        assert not LevelUpEngine._is_asi_level(level, "Wizard")

    def test_non_asi_level(self):
        assert not LevelUpEngine._is_asi_level(2, "Fighter")
        assert not LevelUpEngine._is_asi_level(7, "Wizard")


# ─── Test: Spell Slots ───────────────────────────────────────────────


class TestSpellSlots:
    def test_wizard_gains_spell_slots(self):
        """Wizard 1→2: spell slots change from [2] to [3]."""
        char = make_character(class_name="Wizard", level=1, con=10, hp=6)
        char.hit_dice_type = "d6"
        char.character_class.hit_dice = "1d6"
        char.spell_slots = {1: 2}
        engine = LevelUpEngine(make_mock_manager(make_wizard_def()))
        result = engine.level_up(char)
        assert result.spell_slots_changed
        assert char.spell_slots == {1: 3}

    def test_wizard_gains_new_slot_level(self):
        """Wizard 2→3: gains 2nd level slots."""
        char = make_character(class_name="Wizard", level=2, con=10, hp=10)
        char.hit_dice_type = "d6"
        char.character_class.hit_dice = "2d6"
        char.spell_slots = {1: 3}
        engine = LevelUpEngine(make_mock_manager(make_wizard_def()))
        result = engine.level_up(char)
        assert result.spell_slots_changed
        assert char.spell_slots == {1: 4, 2: 2}

    def test_fighter_no_spell_slots(self):
        """Non-caster classes have no spell slot changes."""
        char = make_character(level=1)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert not result.spell_slots_changed

    def test_ranger_half_caster_starts_at_2(self):
        """Ranger gains spell slots starting at level 2."""
        char = make_character(class_name="Ranger", level=1, con=14, hp=12)
        char.character_class.hit_dice = "1d10"
        engine = LevelUpEngine(make_mock_manager(make_ranger_def()))
        result = engine.level_up(char)
        assert result.spell_slots_changed
        assert char.spell_slots == {1: 2}

    def test_spell_slots_no_change_same_level(self):
        """If spell slots are already correct, no change reported."""
        char = make_character(class_name="Wizard", level=1, con=10, hp=6)
        char.hit_dice_type = "d6"
        char.character_class.hit_dice = "1d6"
        char.spell_slots = {1: 3}  # Already at level 2 slots
        engine = LevelUpEngine(make_mock_manager(make_wizard_def()))
        result = engine.level_up(char)
        assert not result.spell_slots_changed


# ─── Test: Proficiency Bonus ──────────────────────────────────────────


class TestProficiencyBonus:
    def test_prof_bonus_unchanged_level_1_to_2(self):
        char = make_character(level=1)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert not result.proficiency_bonus_changed
        assert char.proficiency_bonus == 2

    def test_prof_bonus_changes_level_4_to_5(self):
        """Proficiency bonus increases at level 5 (from +2 to +3)."""
        char = make_character(level=4, hp=40)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert result.proficiency_bonus_changed
        assert char.proficiency_bonus == 3


# ─── Test: Multi-Level Progression ────────────────────────────────────


class TestMultiLevelProgression:
    """Test leveling up through multiple levels sequentially."""

    def test_fighter_1_to_5(self):
        """Level a Fighter from 1 to 5 sequentially."""
        char = make_character(level=1, con=14, hp=12)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))

        # Track HP gains
        total_hp_gained = 0
        for target_level in range(2, 6):
            result = engine.level_up(char)
            assert result.new_level == target_level
            total_hp_gained += result.hp_gained

        assert char.character_class.level == 5
        assert char.hit_points_max == 12 + total_hp_gained
        assert char.proficiency_bonus == 3  # +3 at level 5
        assert char.hit_dice_remaining == "5d10"

    def test_wizard_1_to_5_with_spells(self):
        """Level a Wizard from 1 to 5, tracking spell slot progression."""
        char = make_character(class_name="Wizard", level=1, con=10, hp=6)
        char.hit_dice_type = "d6"
        char.character_class.hit_dice = "1d6"
        char.spell_slots = {1: 2}  # Initial wizard slots
        engine = LevelUpEngine(make_mock_manager(make_wizard_def()))

        for _ in range(4):  # level up to 5
            engine.level_up(char)

        assert char.character_class.level == 5
        assert char.spell_slots == {1: 4, 2: 3, 3: 2}


# ─── Test: Error Handling ─────────────────────────────────────────────


class TestErrorHandling:
    def test_no_rulebook_loaded(self):
        manager = MagicMock()
        manager.get_class.return_value = None
        char = make_character()
        engine = LevelUpEngine(manager)
        with pytest.raises(LevelUpError, match="not found in loaded rulebooks"):
            engine.level_up(char)

    def test_max_level_20(self):
        char = make_character(level=20, hp=200)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        with pytest.raises(LevelUpError, match="maximum level"):
            engine.level_up(char)

    def test_summary_format(self):
        """Summary should include character name and new level."""
        char = make_character(name="Aldric", level=1)
        engine = LevelUpEngine(make_mock_manager(make_fighter_def()))
        result = engine.level_up(char)
        assert "Aldric" in result.summary
        assert "level 2" in result.summary
        assert "Fighter" in result.summary
