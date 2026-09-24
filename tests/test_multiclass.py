"""Tests for multiclass Character model support.

Covers: model construction, properties, serialization roundtrip,
backward compatibility migration, level-up with class_name, and
DDB mapper multiclass import preservation.
"""

from __future__ import annotations

import json

import pytest

from dm20_protocol.models import Character, CharacterClass, Race


# =============================================================================
# Model Construction & Properties
# =============================================================================


class TestMulticlassModel:
    """Test multiclass Character model features."""

    def test_single_class_via_classes(self):
        """Construct single-class character with classes list."""
        char = Character(
            name="Fighter",
            classes=[CharacterClass(name="Fighter", level=5, hit_dice="d10")],
            race=Race(name="Human"),
        )
        assert len(char.classes) == 1
        assert char.total_level == 5
        assert char.is_multiclass is False
        assert char.class_string() == "Fighter 5"

    def test_multiclass_construction(self):
        """Construct multiclass character."""
        char = Character(
            name="Hybrid",
            classes=[
                CharacterClass(name="Fighter", level=5, hit_dice="d10"),
                CharacterClass(name="Wizard", level=3, hit_dice="d6"),
            ],
            race=Race(name="Elf"),
        )
        assert len(char.classes) == 2
        assert char.total_level == 8
        assert char.is_multiclass is True
        assert char.class_string() == "Fighter 5 / Wizard 3"

    def test_character_class_property_returns_first(self):
        """Backward compat property returns classes[0]."""
        char = Character(
            name="Multi",
            classes=[
                CharacterClass(name="Rogue", level=4, hit_dice="d8"),
                CharacterClass(name="Fighter", level=2, hit_dice="d10"),
            ],
            race=Race(name="Human"),
        )
        assert char.character_class.name == "Rogue"
        assert char.character_class.level == 4

    def test_character_class_property_is_reference(self):
        """Mutations via property affect classes[0] directly."""
        char = Character(
            name="Mutable",
            classes=[CharacterClass(name="Fighter", level=3, hit_dice="d10")],
            race=Race(name="Human"),
        )
        char.character_class.level = 7
        assert char.classes[0].level == 7

    def test_proficiency_bonus_uses_total_level(self):
        """Proficiency bonus calculated from total level."""
        char = Character(
            name="Prof Test",
            classes=[
                CharacterClass(name="Fighter", level=3, hit_dice="d10"),
                CharacterClass(name="Wizard", level=2, hit_dice="d6"),
            ],
            race=Race(name="Human"),
        )
        # Total level 5: proficiency = 2 + (5-1)//4 = 3
        assert char.proficiency_bonus == 3

    def test_three_classes(self):
        """Support three-class multiclass."""
        char = Character(
            name="Triple",
            classes=[
                CharacterClass(name="Fighter", level=5, hit_dice="d10"),
                CharacterClass(name="Wizard", level=3, hit_dice="d6"),
                CharacterClass(name="Rogue", level=2, hit_dice="d8"),
            ],
            race=Race(name="Half-Elf"),
        )
        assert char.total_level == 10
        assert char.is_multiclass is True
        assert char.class_string() == "Fighter 5 / Wizard 3 / Rogue 2"


# =============================================================================
# Backward Compatibility Migration
# =============================================================================


class TestMigration:
    """Test automatic migration from old character_class to classes."""

    def test_migrate_from_character_class_kwarg(self):
        """Old-style character_class= kwarg gets migrated."""
        char = Character(
            name="Old Style",
            character_class=CharacterClass(name="Wizard", level=10, hit_dice="d6"),
            race=Race(name="Elf"),
        )
        assert len(char.classes) == 1
        assert char.classes[0].name == "Wizard"
        assert char.classes[0].level == 10

    def test_migrate_from_dict_with_character_class(self):
        """Old-style dict with character_class key gets migrated."""
        data = {
            "name": "Dict Style",
            "character_class": {"name": "Cleric", "level": 7, "hit_dice": "d8"},
            "race": {"name": "Dwarf"},
        }
        char = Character(**data)
        assert len(char.classes) == 1
        assert char.classes[0].name == "Cleric"

    def test_new_format_not_migrated(self):
        """New-style classes list passes through unchanged."""
        char = Character(
            name="New Style",
            classes=[CharacterClass(name="Paladin", level=6, hit_dice="d10")],
            race=Race(name="Human"),
        )
        assert len(char.classes) == 1
        assert char.classes[0].name == "Paladin"


# =============================================================================
# Serialization Roundtrip
# =============================================================================


class TestSerialization:
    """Test JSON serialization and deserialization."""

    def test_multiclass_serialization_roundtrip(self):
        """Multiclass character survives JSON roundtrip."""
        char = Character(
            name="Roundtrip",
            classes=[
                CharacterClass(name="Fighter", level=5, hit_dice="d10"),
                CharacterClass(name="Wizard", level=3, hit_dice="d6", subclass="Evocation"),
            ],
            race=Race(name="Human"),
        )
        data = char.model_dump()
        restored = Character(**data)

        assert len(restored.classes) == 2
        assert restored.classes[0].name == "Fighter"
        assert restored.classes[1].name == "Wizard"
        assert restored.classes[1].subclass == "Evocation"
        assert restored.total_level == 8

    def test_json_output_uses_classes_key(self):
        """Serialized JSON uses 'classes' not 'character_class'."""
        char = Character(
            name="JSON Test",
            classes=[CharacterClass(name="Bard", level=4, hit_dice="d8")],
            race=Race(name="Half-Elf"),
        )
        json_str = char.model_dump_json()
        data = json.loads(json_str)

        assert "classes" in data
        assert "character_class" not in data
        assert data["classes"][0]["name"] == "Bard"

    def test_old_json_loads_correctly(self):
        """JSON with old character_class key loads via migration."""
        old_json = {
            "name": "Legacy Character",
            "character_class": {"name": "Ranger", "level": 8, "hit_dice": "d10"},
            "race": {"name": "Wood Elf"},
        }
        char = Character(**old_json)
        assert char.classes[0].name == "Ranger"
        assert char.total_level == 8


# =============================================================================
# DDB Mapper Multiclass Preservation
# =============================================================================


class TestDDBMulticlassImport:
    """Test that DDB mapper preserves all classes."""

    def test_multiclass_ddb_import(self):
        """DDB mapper maps all classes, not just primary."""
        from dm20_protocol.importers.dndbeyond.mapper import map_identity

        ddb = {
            "name": "Multiclass DDB",
            "race": {"fullName": "Human"},
            "classes": [
                {
                    "level": 3,
                    "definition": {"name": "Rogue"},
                    "subclassDefinition": {"name": "Thief"},
                },
                {
                    "level": 5,
                    "definition": {"name": "Fighter"},
                    "subclassDefinition": {"name": "Champion"},
                },
            ],
        }
        result, warnings = map_identity(ddb)

        # Primary (highest level) should be first
        assert result["classes"][0].name == "Fighter"
        assert result["classes"][0].level == 5
        assert result["classes"][0].subclass == "Champion"

        # Secondary should be preserved
        assert result["classes"][1].name == "Rogue"
        assert result["classes"][1].level == 3
        assert result["classes"][1].subclass == "Thief"

    def test_full_import_multiclass_character(self):
        """Full import pipeline produces multiclass Character."""
        from dm20_protocol.importers.dndbeyond.mapper import map_ddb_to_character

        ddb = {
            "name": "Full Import",
            "race": {"fullName": "Half-Elf"},
            "classes": [
                {
                    "level": 4,
                    "definition": {"name": "Bard"},
                    "classFeatures": [],
                },
                {
                    "level": 6,
                    "definition": {"name": "Paladin"},
                    "classFeatures": [],
                },
            ],
            "stats": [
                {"id": 1, "value": 14},
                {"id": 2, "value": 12},
                {"id": 3, "value": 13},
                {"id": 4, "value": 10},
                {"id": 5, "value": 10},
                {"id": 6, "value": 16},
            ],
            "bonusStats": [],
            "overrideStats": [],
            "modifiers": {},
            "baseHitPoints": 55,
            "bonusHitPoints": 0,
            "removedHitPoints": 0,
            "temporaryHitPoints": 0,
            "inventory": [],
            "classSpells": [],
        }
        result = map_ddb_to_character(ddb)
        char = result.character

        assert char.is_multiclass is True
        assert char.total_level == 10
        assert char.classes[0].name == "Paladin"  # Higher level first
        assert char.classes[1].name == "Bard"


# =============================================================================
# Level-Up Engine Multiclass
# =============================================================================


class TestLevelUpMulticlass:
    """Test level-up engine with multiclass characters."""

    @pytest.fixture
    def mock_rulebook(self):
        """Create a minimal mock RulebookManager."""
        from unittest.mock import MagicMock

        from dm20_protocol.rulebooks.models import ClassDefinition, ClassLevelInfo

        rm = MagicMock()

        fighter_def = ClassDefinition(
            index="fighter",
            name="Fighter",
            hit_die=10,
            saving_throws=["strength", "constitution"],
            subclass_level=3,
            subclasses=["Champion", "Battle Master"],
            class_levels={
                6: ClassLevelInfo(
                    level=6,
                    proficiency_bonus=3,
                    features=["Extra Attack (2)"],
                    feature_details={"Extra Attack (2)": "You can attack three times."},
                ),
            },
        )
        wizard_def = ClassDefinition(
            index="wizard",
            name="Wizard",
            hit_die=6,
            saving_throws=["intelligence", "wisdom"],
            subclass_level=2,
            subclasses=["Evocation", "Abjuration"],
            class_levels={},
        )

        def get_class_side_effect(name):
            mapping = {"fighter": fighter_def, "wizard": wizard_def}
            return mapping.get(name)

        rm.get_class.side_effect = get_class_side_effect
        return rm

    def test_level_up_specific_class(self, mock_rulebook):
        """Level up a specific class in multiclass character."""
        from dm20_protocol.level_up_engine import LevelUpEngine

        char = Character(
            name="Multi Fighter",
            classes=[
                CharacterClass(name="Fighter", level=5, hit_dice="d10"),
                CharacterClass(name="Wizard", level=3, hit_dice="d6"),
            ],
            race=Race(name="Human"),
        )

        engine = LevelUpEngine(mock_rulebook)
        result = engine.level_up(char, class_name="Fighter")

        assert char.classes[0].level == 6
        assert char.classes[1].level == 3  # Unchanged
        assert char.total_level == 9
        assert result.new_level == 6

    def test_level_up_default_primary(self, mock_rulebook):
        """Without class_name, levels up primary (first) class."""
        from dm20_protocol.level_up_engine import LevelUpEngine

        char = Character(
            name="Default",
            classes=[
                CharacterClass(name="Fighter", level=5, hit_dice="d10"),
                CharacterClass(name="Wizard", level=3, hit_dice="d6"),
            ],
            race=Race(name="Human"),
        )

        engine = LevelUpEngine(mock_rulebook)
        result = engine.level_up(char)

        assert char.classes[0].level == 6  # Primary leveled up
        assert char.classes[1].level == 3  # Secondary unchanged

    def test_level_up_total_level_cap(self, mock_rulebook):
        """Total level 20 cap prevents any further level-up."""
        from dm20_protocol.level_up_engine import LevelUpEngine, LevelUpError

        char = Character(
            name="Maxed",
            classes=[
                CharacterClass(name="Fighter", level=15, hit_dice="d10"),
                CharacterClass(name="Wizard", level=5, hit_dice="d6"),
            ],
            race=Race(name="Human"),
        )

        engine = LevelUpEngine(mock_rulebook)
        with pytest.raises(LevelUpError, match="maximum level"):
            engine.level_up(char, class_name="Fighter")

    def test_level_up_unknown_class_raises(self, mock_rulebook):
        """Level up with class not in rulebooks raises error."""
        from dm20_protocol.level_up_engine import LevelUpEngine, LevelUpError

        char = Character(
            name="Wrong",
            classes=[CharacterClass(name="Fighter", level=5, hit_dice="d10")],
            race=Race(name="Human"),
        )

        engine = LevelUpEngine(mock_rulebook)
        with pytest.raises(LevelUpError, match="not found in loaded rulebooks"):
            engine.level_up(char, class_name="Warlock")

    def test_multiclass_dip_adds_new_class(self, mock_rulebook):
        """Level up with a new valid class adds it as a multiclass dip."""
        from dm20_protocol.level_up_engine import LevelUpEngine

        char = Character(
            name="Dip Test",
            classes=[CharacterClass(name="Fighter", level=5, hit_dice="d10")],
            race=Race(name="Human"),
        )

        engine = LevelUpEngine(mock_rulebook)
        result = engine.level_up(char, class_name="Wizard")

        assert len(char.classes) == 2
        assert char.classes[1].name == "Wizard"
        assert char.classes[1].level == 1
        assert char.total_level == 6
        assert result.new_level == 1
        assert "Multiclass" in result.summary


# =============================================================================
# Adventure Parser Tests
# =============================================================================


class TestAdventureParserCaseInsensitive:
    """Test adventure parser case-insensitive ID handling."""

    def test_source_file_uses_lowercase(self):
        """source_file in ModuleStructure uses lowercase ID."""
        from dm20_protocol.adventures.parser import AdventureParser

        parser = AdventureParser(cache_dir=pytest.importorskip("pathlib").Path("/tmp/test_cache"))

        # Nested format
        data = {
            "data": [
                {
                    "name": "Test Adventure",
                    "source": "TEST",
                    "data": [],
                }
            ]
        }
        result = parser._parse_adventure_data("BGDIA", data)
        assert result.source_file == "adventure-bgdia.json"

    def test_flat_format_parsing(self):
        """Flat format (list of sections) is parsed correctly."""
        from dm20_protocol.adventures.parser import AdventureParser

        parser = AdventureParser(cache_dir=pytest.importorskip("pathlib").Path("/tmp/test_cache"))

        data = [
            {
                "type": "section",
                "name": "Chapter 1: The Beginning",
                "entries": ["Some text here."],
            },
            {
                "type": "section",
                "name": "Chapter 2: The Middle",
                "entries": ["More text."],
            },
        ]
        result = parser._parse_adventure_data("TestAdv", data)
        assert len(result.chapters) == 2
        assert result.chapters[0].name == "Chapter 1: The Beginning"
        assert result.title == "Chapter 1: The Beginning"


# =============================================================================
# Import Summary Tests
# =============================================================================


class TestFormatImportSummary:
    """Test _format_import_summary with real Character objects."""

    def test_single_class_summary(self):
        """Format summary for single-class character."""
        from dm20_protocol.importers.base import ImportResult
        from dm20_protocol.main import _format_import_summary

        char = Character(
            name="Test Hero",
            classes=[CharacterClass(name="Fighter", level=5, hit_dice="d10", subclass="Champion")],
            race=Race(name="Human"),
            hit_points_max=45,
            hit_points_current=45,
            armor_class=18,
        )
        result = ImportResult(
            character=char,
            mapped_fields=["name", "classes", "race", "hit_points_current", "armor_class"],
            unmapped_fields=[],
            warnings=[],
            source="url",
        )
        summary = _format_import_summary(result)

        assert "Test Hero" in summary
        assert "Fighter 5" in summary
        assert "Champion" in summary
        assert "Human" in summary
        assert "Combat: 45/45, 18" in summary  # report format: HP, then AC

    def test_multiclass_summary(self):
        """Format summary for multiclass character."""
        from dm20_protocol.importers.base import ImportResult
        from dm20_protocol.main import _format_import_summary

        char = Character(
            name="Multi Hero",
            classes=[
                CharacterClass(name="Fighter", level=5, hit_dice="d10"),
                CharacterClass(name="Wizard", level=3, hit_dice="d6", subclass="Evocation"),
            ],
            race=Race(name="Elf"),
        )
        result = ImportResult(
            character=char,
            mapped_fields=["name", "classes"],
            unmapped_fields=[],
            warnings=["Test warning"],
            source="url",
        )
        summary = _format_import_summary(result)

        assert "Multi Hero" in summary
        assert "Fighter 5 / Wizard 3" in summary
        assert "Evocation" in summary
        assert "Test warning" in summary


# =============================================================================
# Character Builder Multiclass Creation
# =============================================================================


class TestCharacterBuilderMulticlass:
    """Test creating multiclass characters from scratch."""

    @pytest.fixture
    def mock_builder(self):
        """Create a CharacterBuilder with mock RulebookManager."""
        from unittest.mock import MagicMock

        from dm20_protocol.character_builder import CharacterBuilder
        from dm20_protocol.rulebooks.models import (
            ClassDefinition,
            ClassLevelInfo,
            RaceDefinition,
        )

        rm = MagicMock()

        fighter_def = ClassDefinition(
            index="fighter",
            name="Fighter",
            hit_die=10,
            saving_throws=["strength", "constitution"],
            subclass_level=3,
            class_levels={
                1: ClassLevelInfo(
                    level=1,
                    proficiency_bonus=2,
                    features=["Fighting Style", "Second Wind"],
                    feature_details={},
                ),
            },
        )
        wizard_def = ClassDefinition(
            index="wizard",
            name="Wizard",
            hit_die=6,
            saving_throws=["intelligence", "wisdom"],
            subclass_level=2,
            class_levels={
                1: ClassLevelInfo(
                    level=1,
                    proficiency_bonus=2,
                    features=["Arcane Recovery"],
                    feature_details={},
                ),
            },
        )

        def get_class_side_effect(name):
            return {"fighter": fighter_def, "wizard": wizard_def}.get(name)

        rm.get_class.side_effect = get_class_side_effect
        return CharacterBuilder(rm)

    def test_add_classes_to_character(self, mock_builder):
        """Add secondary classes to an already-built character."""
        char = Character(
            name="Multi Builder",
            classes=[CharacterClass(name="Fighter", level=5, hit_dice="d10")],
            race=Race(name="Human"),
            hit_points_max=40,
            hit_points_current=40,
        )

        mock_builder.add_classes(char, [{"name": "Wizard", "level": 3}])

        assert len(char.classes) == 2
        assert char.classes[1].name == "Wizard"
        assert char.classes[1].level == 3
        assert char.total_level == 8
        assert char.hit_points_max > 40  # HP increased
        # Wizard level 1 feature should be added
        assert any(f.name == "Arcane Recovery" for f in char.features)

    def test_add_classes_level_cap(self, mock_builder):
        """Adding classes beyond total level 20 raises error."""
        from dm20_protocol.character_builder import CharacterBuilderError

        char = Character(
            name="Capped",
            classes=[CharacterClass(name="Fighter", level=18, hit_dice="d10")],
            race=Race(name="Human"),
        )

        with pytest.raises(CharacterBuilderError, match="exceed 20"):
            mock_builder.add_classes(char, [{"name": "Wizard", "level": 5}])
