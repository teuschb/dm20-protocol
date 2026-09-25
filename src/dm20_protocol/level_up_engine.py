"""Level-Up Engine — handle character progression from one level to the next.

Given a Character and a RulebookManager, the engine increments level,
calculates HP increase, adds class features, updates spell slots, handles
ASI choices, and manages subclass selection.
"""

from __future__ import annotations

import random
from typing import Any

from pydantic import BaseModel

from .models import AbilityScore, Character, Feature
from .rulebooks.manager import RulebookManager
from .rulebooks.models import ClassDefinition


# Standard ASI levels (most classes)
STANDARD_ASI_LEVELS = {4, 8, 12, 16, 19}

# Fighter gets extra ASI at levels 6 and 14
FIGHTER_EXTRA_ASI_LEVELS = {6, 14}

# Rogue gets extra ASI at level 10
ROGUE_EXTRA_ASI_LEVEL = {10}

# Ability abbreviation → full name
ABILITY_NAMES = {
    "STR": "strength",
    "DEX": "dexterity",
    "CON": "constitution",
    "INT": "intelligence",
    "WIS": "wisdom",
    "CHA": "charisma",
}

ALL_ABILITIES = set(ABILITY_NAMES.values())

# Flat max-HP bonuses gained on every level-up, keyed by normalized trait/feat
# name (the text before any " (" or ":" suffix, lowercased).
# Dwarven Toughness: +1 HP per level (2024 Dwarf). Tough feat: +2 HP per level.
FLAT_HP_PER_LEVEL = {
    "dwarven toughness": 1,
    "tough": 2,
}

# Maximum ability score (without magic items)
MAX_ABILITY_SCORE = 20


class LevelUpError(Exception):
    """Raised when level-up cannot proceed."""


class LevelUpResult(BaseModel):
    """Summary of changes applied during level-up."""

    new_level: int
    hp_gained: int
    features_added: list[str]
    spell_slots_changed: bool
    asi_applied: dict[str, int] | None = None
    subclass_set: str | None = None
    proficiency_bonus_changed: bool
    summary: str


class LevelUpEngine:
    """Handle character level progression using rulebook data."""

    def __init__(self, rulebook_manager: RulebookManager) -> None:
        self.rm = rulebook_manager

    def level_up(
        self,
        character: Character,
        *,
        class_name: str | None = None,
        hp_method: str = "average",
        asi_choices: dict[str, int] | None = None,
        subclass: str | None = None,
        new_spells: list[str] | None = None,
    ) -> LevelUpResult:
        """Level up a character by one level.

        Args:
            character: The character to level up (modified in-place).
            class_name: Which class to level up (required for multiclass).
                        If None, levels up the primary (first) class.
            hp_method: "average" (default, PHB standard) or "roll".
            asi_choices: Ability score improvements, e.g. {"strength": 2}
                         or {"strength": 1, "dexterity": 1}. Total must be 2.
            subclass: Subclass name to set (required at subclass_level).
            new_spells: List of spell names to learn (informational, added to notes).

        Returns:
            LevelUpResult with summary of all changes.

        Raises:
            LevelUpError: If level-up cannot proceed.
        """
        # Check total level cap before proceeding
        total_before = character.total_level
        if total_before >= 20:
            raise LevelUpError("Character is already at maximum level (20).")

        # Find the target class to level up (may add a new class for multiclass dip)
        target_class, is_new_class = self._find_target_class(character, class_name)

        if is_new_class:
            # Multiclass dip: class already added at level 1 by _find_target_class.
            # total_level is now total_before + 1 (the new class at level 1).
            new_level = 1
            current_level = 0
        else:
            current_level = target_class.level
            new_level = current_level + 1
            target_class.level = new_level

        # Look up class definition
        class_def = self._get_class_def(target_class.name)

        old_prof_bonus = character.proficiency_bonus
        changes: list[str] = []

        # 1. Report level change
        if is_new_class:
            changes.append(f"Multiclass: added {target_class.name} (level 1)")
        else:
            changes.append(f"{target_class.name} Level: {current_level} -> {new_level}")

        # 2. Calculate and apply HP increase
        hp_gained = self._calculate_hp_increase(
            class_def.hit_die, character, hp_method
        )
        character.hit_points_max += hp_gained
        character.hit_points_current += hp_gained
        changes.append(f"HP: +{hp_gained} (max now {character.hit_points_max})")

        # 3. Update hit dice
        hit_dice_type = f"d{class_def.hit_die}"
        character.hit_dice_type = hit_dice_type
        total_level = character.total_level
        character.hit_dice_remaining = f"{total_level}{hit_dice_type}"
        target_class.hit_dice = f"{new_level}{hit_dice_type}"

        # 4. Add class features from this level
        features_added = self._add_level_features(character, class_def, new_level)
        if features_added:
            changes.append(f"Features: {', '.join(features_added)}")

        # 5. Handle subclass at the appropriate level
        subclass_set = None
        if new_level == class_def.subclass_level:
            if subclass:
                subclass_set = self._set_subclass(target_class, class_def, subclass)
                changes.append(f"Subclass: {subclass_set}")
            else:
                changes.append(
                    f"NOTE: Level {new_level} is the subclass selection level "
                    f"for {class_def.name}. Available: {', '.join(class_def.subclasses) or 'check rulebook'}. "
                    f"Use update_character to set subclass later."
                )

        # 6. Handle ASI at appropriate levels
        asi_applied = None
        if self._is_asi_level(new_level, class_def.name):
            if asi_choices:
                asi_applied = self._apply_asi(character, asi_choices)
                asi_desc = ", ".join(
                    f"{k.capitalize()} +{v}" for k, v in asi_applied.items()
                )
                changes.append(f"ASI: {asi_desc}")
            else:
                changes.append(
                    f"NOTE: Level {new_level} grants an Ability Score Improvement. "
                    "Use level_up_character with asi_choices to apply it later, "
                    "or use update_character to adjust ability scores."
                )

        # 7. Update spell slots for casters
        spell_slots_changed = self._update_spell_slots(character, class_def, new_level)
        if spell_slots_changed:
            slots_str = ", ".join(
                f"L{k}: {v}" for k, v in sorted(character.spell_slots.items())
            )
            changes.append(f"Spell slots: {slots_str}")

        # 8. Handle new spells (informational)
        if new_spells:
            changes.append(f"New spells noted: {', '.join(new_spells)}")

        # 9. Proficiency bonus — model_validator only runs on construction,
        # so we must recalculate manually after mutating level in-place.
        character.proficiency_bonus = 2 + (character.total_level - 1) // 4
        new_prof_bonus = character.proficiency_bonus
        prof_changed = new_prof_bonus != old_prof_bonus
        if prof_changed:
            changes.append(
                f"Proficiency bonus: +{old_prof_bonus} -> +{new_prof_bonus}"
            )

        # Build summary
        summary = (
            f"{character.name} advanced to {target_class.name} level {new_level}"
            f" (total level {character.total_level})!\n"
            + "\n".join(f"  - {c}" for c in changes)
        )

        return LevelUpResult(
            new_level=new_level,
            hp_gained=hp_gained,
            features_added=features_added,
            spell_slots_changed=spell_slots_changed,
            asi_applied=asi_applied,
            subclass_set=subclass_set,
            proficiency_bonus_changed=prof_changed,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # HP Calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _flat_hp_per_level(character: Character) -> int:
        """Sum flat max-HP-per-level bonuses from traits and feats.

        Looks at race traits, structured features and the legacy text list so
        a bonus is found wherever the sheet records it, but counts each bonus
        only once.
        """
        names: list[str] = list(character.race.traits)
        names.extend(f.name for f in character.features)
        names.extend(character.features_and_traits)

        found: set[str] = set()
        for raw in names:
            key = raw.split(" (")[0].split(":")[0].strip().lower()
            if key in FLAT_HP_PER_LEVEL:
                found.add(key)
        return sum(FLAT_HP_PER_LEVEL[k] for k in found)

    @staticmethod
    def _calculate_hp_increase(
        hit_die: int, character: Character, method: str
    ) -> int:
        """Calculate HP gained for one level.

        Average: hit_die // 2 + 1 + CON mod (PHB standard, minimum 1).
        Roll: random 1-hit_die + CON mod (minimum 1).
        Flat per-level bonuses (Dwarven Toughness, Tough) are added on top
        after the minimum-1 floor.
        """
        con_mod = character.abilities.get(
            "constitution", AbilityScore(score=10)
        ).mod
        flat = LevelUpEngine._flat_hp_per_level(character)

        if method == "average":
            avg_roll = hit_die // 2 + 1
            return max(avg_roll + con_mod, 1) + flat
        elif method == "roll":
            roll = random.randint(1, hit_die)
            return max(roll + con_mod, 1) + flat
        else:
            raise LevelUpError(
                f"Unknown hp_method: '{method}'. Use 'average' or 'roll'."
            )

    # ------------------------------------------------------------------
    # Features
    # ------------------------------------------------------------------

    @staticmethod
    def _add_level_features(
        character: Character, class_def: ClassDefinition, level: int
    ) -> list[str]:
        """Add features from ClassDefinition.class_levels for this level."""
        level_info = class_def.class_levels.get(level)
        if not level_info:
            return []

        added: list[str] = []
        for feat_name in level_info.features:
            desc = level_info.feature_details.get(feat_name, "")
            feature = Feature(
                name=feat_name,
                source=f"{class_def.name} {level}",
                description=desc,
                level_gained=level,
            )
            character.features.append(feature)
            if feat_name not in character.features_and_traits:
                character.features_and_traits.append(feat_name)
            added.append(feat_name)

        return added

    # ------------------------------------------------------------------
    # Subclass
    # ------------------------------------------------------------------

    @staticmethod
    def _set_subclass(
        target_class: "CharacterClass", class_def: ClassDefinition, subclass: str
    ) -> str:
        """Set the target class's subclass, validating against available options."""
        from .models import CharacterClass as _CC  # noqa: F811 - for type hint only

        # Normalize for comparison
        subclass_lower = subclass.lower().replace(" ", "-").replace("_", "-")

        if class_def.subclasses:
            valid = [s.lower() for s in class_def.subclasses]
            if subclass_lower not in valid:
                raise LevelUpError(
                    f"Invalid subclass '{subclass}' for {class_def.name}. "
                    f"Available: {', '.join(class_def.subclasses)}"
                )

        target_class.subclass = subclass
        return subclass

    # ------------------------------------------------------------------
    # ASI
    # ------------------------------------------------------------------

    @staticmethod
    def _is_asi_level(level: int, class_name: str) -> bool:
        """Check if this level grants an ASI."""
        class_lower = class_name.lower()
        asi_levels = set(STANDARD_ASI_LEVELS)

        if class_lower == "fighter":
            asi_levels |= FIGHTER_EXTRA_ASI_LEVELS
        elif class_lower == "rogue":
            asi_levels |= ROGUE_EXTRA_ASI_LEVEL

        return level in asi_levels

    @staticmethod
    def _apply_asi(
        character: Character, choices: dict[str, int]
    ) -> dict[str, int]:
        """Apply Ability Score Improvement choices.

        Validates:
        - Total bonus is exactly 2
        - Each individual bonus is 1 or 2
        - Abilities exist
        - Scores don't exceed MAX_ABILITY_SCORE
        """
        # Normalize ability names
        normalized: dict[str, int] = {}
        for ability, bonus in choices.items():
            name = ABILITY_NAMES.get(ability.upper(), ability.lower())
            if name not in ALL_ABILITIES:
                raise LevelUpError(
                    f"Unknown ability: '{ability}'. "
                    f"Valid: {', '.join(sorted(ALL_ABILITIES))}"
                )
            if bonus not in (1, 2):
                raise LevelUpError(
                    f"Each ASI bonus must be 1 or 2 (got {ability}={bonus})"
                )
            normalized[name] = bonus

        total = sum(normalized.values())
        if total != 2:
            raise LevelUpError(
                f"ASI total must be exactly 2 (got {total}). "
                "Use {{\"ability\": 2}} or {{\"ability1\": 1, \"ability2\": 1}}."
            )

        # Apply bonuses
        applied: dict[str, int] = {}
        for ability, bonus in normalized.items():
            current = character.abilities.get(
                ability, AbilityScore(score=10)
            )
            new_score = min(current.score + bonus, MAX_ABILITY_SCORE)
            actual_bonus = new_score - current.score
            character.abilities[ability] = AbilityScore(score=new_score)
            if actual_bonus > 0:
                applied[ability] = actual_bonus

        return applied

    # ------------------------------------------------------------------
    # Spell Slots
    # ------------------------------------------------------------------

    @staticmethod
    def _update_spell_slots(
        character: Character, class_def: ClassDefinition, level: int
    ) -> bool:
        """Update spell slot maximums for the new level.

        Returns True if spell slots changed.
        """
        if not class_def.spellcasting or not class_def.spellcasting.spell_slots:
            return False

        slots_list = class_def.spellcasting.spell_slots.get(level)
        if not slots_list:
            return False

        new_slots: dict[int, int] = {}
        for idx, count in enumerate(slots_list):
            spell_level = idx + 1
            if count > 0:
                new_slots[spell_level] = count

        changed = new_slots != character.spell_slots
        if changed:
            character.spell_slots = new_slots

        return changed

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def _find_target_class(
        self, character: Character, class_name: str | None
    ) -> tuple["CharacterClass", bool]:
        """Find the class to level up, or add a new class (multiclass dip).

        Returns:
            Tuple of (target_class, is_new_class).
            is_new_class=True means this is a multiclass dip into a brand new class.
        """
        from .models import CharacterClass as _CC

        if class_name is None:
            return character.classes[0], False

        # Search by name (case-insensitive)
        name_lower = class_name.strip().lower()
        for cls in character.classes:
            if cls.name.lower() == name_lower:
                return cls, False

        # Not found — this is a multiclass dip into a new class.
        # Validate the class exists in rulebooks before adding.
        class_def = self._get_class_def(class_name)
        hit_dice_type = f"d{class_def.hit_die}"
        new_class = _CC(
            name=class_def.name,
            level=1,  # Starts at level 1
            hit_dice=hit_dice_type,
        )
        character.classes.append(new_class)
        return new_class, True

    def _get_class_def(self, class_name: str) -> ClassDefinition:
        """Look up class definition from rulebook manager."""
        index = class_name.strip().lower().replace(" ", "-").replace("_", "-")
        class_def = self.rm.get_class(index)
        if not class_def:
            raise LevelUpError(
                f"Class '{class_name}' not found in loaded rulebooks. "
                "Make sure a rulebook is loaded: load_rulebook source=\"srd\""
            )
        return class_def
