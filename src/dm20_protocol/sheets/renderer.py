"""Render a Character model to a beautiful Markdown character sheet.

Produces YAML frontmatter (structured data for sync) + Markdown body
(human-readable, works in any viewer, optional Meta-Bind hints for Obsidian).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import yaml

from dm20_protocol.models import Character
from dm20_protocol.sheets.parser import CharacterSheetParser
from dm20_protocol.sheets.schema import FIELD_MAPPINGS, SheetSchema

logger = logging.getLogger(__name__)


class CharacterSheetRenderer:
    """Renders Character objects to Markdown files with YAML frontmatter."""

    def __init__(self, sheets_dir: Path) -> None:
        self.sheets_dir = sheets_dir
        self.sheets_dir.mkdir(parents=True, exist_ok=True)

    def render(
        self,
        character: Character,
        *,
        sync_version: int = 1,
        sync_time: str = "",
    ) -> str:
        """Render a character to a complete Markdown string.

        Returns the full document: YAML frontmatter + Markdown body.
        """
        fm = SheetSchema.character_to_frontmatter(
            character, sync_version=sync_version, sync_time=sync_time,
        )
        frontmatter_str = _render_frontmatter(fm)
        body = _render_body(character)
        return f"{frontmatter_str}\n{body}"

    def write(
        self,
        character: Character,
        *,
        sync_version: int = 1,
        sync_time: str = "",
    ) -> tuple[Path, str]:
        """Write a character sheet to disk with atomic write.

        Returns (path, frontmatter_hash) for feedback loop prevention.
        """
        content = self.render(
            character, sync_version=sync_version, sync_time=sync_time,
        )
        filename = _safe_filename(character.name)
        target = self.sheets_dir / f"{filename}.md"

        # Atomic write: temp file in same dir, then rename
        fd, tmp_path = tempfile.mkstemp(
            dir=self.sheets_dir, suffix=".md.tmp", prefix=".sheet_",
        )
        try:
            with open(fd, "w", encoding="utf-8") as f:
                f.write(content)
            Path(tmp_path).replace(target)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

        fm_hash = CharacterSheetParser.frontmatter_hash(content)
        logger.info("Sheet written: %s (hash=%s)", target, fm_hash[:8])
        return target, fm_hash

    def delete(self, character_name: str) -> bool:
        """Delete a character sheet file. Returns True if file existed."""
        filename = _safe_filename(character_name)
        target = self.sheets_dir / f"{filename}.md"
        if target.exists():
            target.unlink()
            logger.info("Sheet deleted: %s", target)
            return True
        return False

    def rename(self, old_name: str, new_name: str) -> Path | None:
        """Handle character rename: delete old sheet, return new path."""
        self.delete(old_name)
        # The caller should re-render with the new name
        return None

    def sheet_path(self, character_name: str) -> Path:
        """Return the expected path for a character's sheet."""
        return self.sheets_dir / f"{_safe_filename(character_name)}.md"


def _safe_filename(name: str) -> str:
    """Convert a character name to a safe filename.

    "Aldric Stormwind" → "Aldric Stormwind"
    "Thog the Destroyer" → "Thog the Destroyer"
    Strips filesystem-unsafe chars but preserves spaces for readability.
    """
    unsafe = set('<>:"/\\|?*')
    return "".join(c for c in name.strip() if c not in unsafe) or "unnamed"


def _render_frontmatter(fm: dict[str, Any]) -> str:
    """Render frontmatter dict to YAML with section comments."""
    lines = ["---"]
    current_section = ""

    for mapping in FIELD_MAPPINGS:
        key = mapping.frontmatter_key
        if key not in fm:
            continue

        # Section header comment
        if mapping.section and mapping.section != current_section:
            if current_section:
                lines.append("")  # blank line between sections
            lines.append(f"# {mapping.section}")
            current_section = mapping.section

        value = fm[key]
        yaml_str = _value_to_yaml_line(key, value)
        lines.append(yaml_str)

    lines.append("---")
    return "\n".join(lines)


def _value_to_yaml_line(key: str, value: Any) -> str:
    """Format a single key-value pair as a YAML line.

    Simple scalars are inlined. Lists and dicts use yaml.dump for
    clean multi-line output.
    """
    if value is None:
        return f"{key}: null"
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key}: {value}"
    if isinstance(value, str):
        # Quote strings that could be misinterpreted by YAML
        if _needs_quoting(value):
            return f'{key}: "{value}"'
        return f"{key}: {value}"
    if isinstance(value, list):
        if not value:
            return f"{key}: []"
        # Short simple lists inline, complex ones multi-line
        if all(isinstance(v, str) for v in value) and len(value) <= 8:
            items = ", ".join(f'"{v}"' if _needs_quoting(v) else v for v in value)
            return f"{key}: [{items}]"
        # Multi-line for complex items
        dumped = yaml.dump({key: value}, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return dumped.rstrip()
    if isinstance(value, dict):
        if not value:
            return f"{key}: {{}}"
        # Simple dicts (like spell_slots {1: 4, 2: 3}) — render value in flow style
        if all(isinstance(v, (int, float, str, bool, type(None))) for v in value.values()):
            # Dump just the value in flow style, then prepend the key
            val_dumped = yaml.dump(value, default_flow_style=True, allow_unicode=True, sort_keys=False).rstrip()
            return f"{key}: {val_dumped}"
        dumped = yaml.dump({key: value}, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return dumped.rstrip()

    # Fallback
    return f"{key}: {value}"


def _needs_quoting(s: str) -> bool:
    """Check if a string needs quoting in YAML."""
    if not s:
        return True
    # YAML special values
    if s.lower() in {"true", "false", "yes", "no", "on", "off", "null", "~"}:
        return True
    # Contains characters that need quoting
    if any(c in s for c in ":{}[]#&*!|>'\"%@`"):
        return True
    # Starts with special char
    if s[0] in "-? ":
        return True
    return False


def _render_body(character: Character) -> str:
    """Render the Markdown body (human-readable, not for sync)."""
    c = character
    cls = c.character_class
    race = c.race

    parts: list[str] = []

    # Title (multiclass-aware)
    if c.is_multiclass:
        class_str = c.class_string()
    else:
        subclass_str = f" ({cls.subclass})" if cls.subclass else ""
        class_str = f"Level {cls.level} {cls.name}{subclass_str}"
    parts.append(f"# {c.name}\n")
    parts.append(
        f"> *{class_str} {race.name}*\n"
        f"> **Player:** {c.player_name or 'N/A'} | "
        f"**Background:** {c.background or 'N/A'} | "
        f"**Alignment:** {c.alignment or 'N/A'}\n"
    )
    parts.append("---\n")

    # Ability Scores
    parts.append("## Ability Scores\n")
    abilities = c.abilities
    header = "| STR | DEX | CON | INT | WIS | CHA |"
    sep = "|:---:|:---:|:---:|:---:|:---:|:---:|"
    vals = []
    for ab_name in ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]:
        ab = abilities.get(ab_name)
        if ab:
            mod = ab.mod
            sign = "+" if mod >= 0 else ""
            vals.append(f"**{ab.score}** ({sign}{mod})")
        else:
            vals.append("—")
    row = "| " + " | ".join(vals) + " |"
    parts.append(f"{header}\n{sep}\n{row}\n")

    # Creation Rolls (if recorded)
    if c.creation_rolls:
        parts.append("### Ability Score Rolls\n")
        parts.append("| Ability | Rolls | Dropped | Total |")
        parts.append("|---------|-------|---------|:-----:|")
        for ability_name in ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"]:
            roll_data = c.creation_rolls.get(ability_name)
            if roll_data and isinstance(roll_data, dict):
                rolls = roll_data.get("rolls", [])
                dropped = roll_data.get("dropped", "")
                total = roll_data.get("total", "")
                rolls_str = ", ".join(str(r) for r in rolls) if rolls else "—"
                parts.append(
                    f"| {ability_name.upper()[:3]} | {rolls_str} | {dropped} | {total} |"
                )
        parts.append("")

    # Combat
    parts.append("## Combat\n")
    prof_bonus = c.proficiency_bonus
    inspiration_str = "Yes" if c.inspiration else "No"
    parts.append(
        f"| AC | HP | Temp HP | Speed | Prof. Bonus | Hit Dice | Inspiration |\n"
        f"|:---:|:---:|:---:|:---:|:---:|:---:|:---:|\n"
        f"| {c.armor_class} | {c.hit_points_current}/{c.hit_points_max} "
        f"| {c.temporary_hit_points} | {c.speed} ft | +{prof_bonus} "
        f"| {c.hit_dice_remaining} | {inspiration_str} |\n"
    )

    # Conditions
    if c.conditions:
        parts.append(f"**Conditions:** {', '.join(c.conditions)}\n")

    # Skills & Proficiencies
    parts.append("## Skills & Proficiencies\n")
    if c.saving_throw_proficiencies:
        st_abbrevs = [_ability_abbrev(s) for s in c.saving_throw_proficiencies]
        parts.append(f"**Saving Throws:** {', '.join(st_abbrevs)}\n")
    if c.skill_proficiencies:
        parts.append(f"**Skills:** {', '.join(sorted(c.skill_proficiencies))}\n")
    if c.tool_proficiencies:
        parts.append(f"**Tools:** {', '.join(sorted(c.tool_proficiencies))}\n")
    if c.languages:
        parts.append(f"**Languages:** {', '.join(sorted(c.languages))}\n")

    # Equipment
    parts.append("## Equipment\n")
    equipped_items: list[str] = []
    for slot, item in c.equipment.items():
        if item is not None:
            slot_label = slot.replace("_", " ").title()
            equipped_items.append(f"- **{slot_label}:** {item.name}")
    if equipped_items:
        parts.append("\n".join(equipped_items) + "\n")
    else:
        parts.append("*No items equipped.*\n")

    # Inventory
    if c.inventory:
        parts.append("### Inventory\n")
        parts.append("| Item | Qty | Type | Weight | Value |")
        parts.append("|------|:---:|------|:------:|------:|")
        for item in c.inventory:
            weight = f"{item.weight} lb" if item.weight else "—"
            value = item.value or "—"
            parts.append(f"| {item.name} | {item.quantity} | {item.item_type} | {weight} | {value} |")
        parts.append("")

    # Spellcasting
    if c.spellcasting_ability or c.spells_known:
        parts.append("## Spellcasting\n")
        if c.spellcasting_ability:
            parts.append(f"**Spellcasting Ability:** {c.spellcasting_ability.title()}\n")

        # Spell Slots
        if c.spell_slots:
            parts.append("### Spell Slots\n")
            levels = sorted(c.spell_slots.keys())
            header_row = "| " + " | ".join(f"Lv {lv}" for lv in levels) + " |"
            sep_row = "|" + "|".join(":---:" for _ in levels) + "|"
            used = c.spell_slots_used
            vals_row = "| " + " | ".join(
                f"{used.get(lv, 0)}/{c.spell_slots[lv]}" for lv in levels
            ) + " |"
            parts.append(f"{header_row}\n{sep_row}\n{vals_row}\n")

        # Spells Known
        if c.spells_known:
            parts.append("### Spells Known\n")
            parts.append("| Spell | Level | School | Prepared |")
            parts.append("|-------|:-----:|--------|:--------:|")
            for spell in sorted(c.spells_known, key=lambda s: (s.level, s.name)):
                lvl = "Cantrip" if spell.level == 0 else str(spell.level)
                prep = "Yes" if spell.prepared else "No"
                parts.append(f"| {spell.name} | {lvl} | {spell.school} | {prep} |")
            parts.append("")

    # Features
    if c.features or c.features_and_traits:
        parts.append("## Features & Traits\n")
        for feat in c.features:
            desc_preview = f" — {feat.description[:80]}..." if len(feat.description) > 80 else (f" — {feat.description}" if feat.description else "")
            uses = f" [{feat.uses_label()}]" if feat.uses_max is not None else ""
            parts.append(f"- **{feat.name}**{uses} (*{feat.source}*){desc_preview}")
        for trait in c.features_and_traits:
            if not any(f.name == trait for f in c.features):
                parts.append(f"- {trait}")
        parts.append("")

    # Description / Bio / Notes
    for section_name, text in [("Description", c.description), ("Bio", c.bio), ("Notes", c.notes)]:
        if text:
            parts.append(f"## {section_name}\n")
            parts.append(f"{text}\n")

    # Footer
    parts.append("---")
    parts.append("*Generated by dm20-protocol — edit YAML frontmatter above to propose changes.*\n")

    return "\n".join(parts)


def _ability_abbrev(name: str) -> str:
    """Convert ability name to 3-letter abbreviation."""
    abbrevs = {
        "strength": "STR", "dexterity": "DEX", "constitution": "CON",
        "intelligence": "INT", "wisdom": "WIS", "charisma": "CHA",
    }
    return abbrevs.get(name.lower(), name.upper()[:3])
