"""
Base models and exceptions for the character import system.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..models import Character


class ImportError(Exception):
    """Raised when a character import fails.

    Provides a user-facing message explaining what went wrong
    and, where possible, how to fix it.
    """


class ImportedField(BaseModel):
    """A field that was successfully imported."""

    name: str = Field(description="Field name")
    summary: str = Field(default="", description="Brief summary of the imported value")


class ImportWarning(BaseModel):
    """A warning generated during import."""

    field: str = Field(description="Field that triggered the warning")
    message: str = Field(description="Human-readable warning message")
    suggestion: str = Field(default="", description="Actionable suggestion to resolve the warning")


class NotImported(BaseModel):
    """A field that could not be imported."""

    field: str = Field(description="Field name that was not imported")
    reason: str = Field(description="Reason why the field was not imported")


class ImportReport(BaseModel):
    """Structured import report with status, imported fields, warnings, and suggestions."""

    status: str = Field(description='Import status: "success", "success_with_warnings", or "failed"')
    character_name: str = Field(description="Name of the imported character")
    imported_fields: list[ImportedField] = Field(
        default_factory=list,
        description="Fields successfully imported with value summaries",
    )
    warnings: list[ImportWarning] = Field(
        default_factory=list,
        description="Non-fatal issues encountered during import",
    )
    not_imported: list[NotImported] = Field(
        default_factory=list,
        description="Fields that could not be imported with reasons",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="Actionable advice for improving the import",
    )

    def format(self) -> str:
        """Format the report as a readable text block.

        Returns:
            Multi-line formatted string suitable for MCP tool response.
        """
        lines: list[str] = []

        # Header
        lines.append(f"D&D Beyond Import Report - {self.character_name}")
        status_display = self.status.upper().replace("_", " ")
        lines.append(f"Status: {status_display}")
        lines.append("")

        # Imported fields
        if self.imported_fields:
            lines.append(f"Imported ({len(self.imported_fields)} fields):")
            # Group by category
            categories: dict[str, list[ImportedField]] = {}
            for field in self.imported_fields:
                cat = _categorize_field(field.name)
                categories.setdefault(cat, []).append(field)

            for cat_name, fields in categories.items():
                summaries = [f.summary if f.summary else f.name for f in fields]
                lines.append(f"  {cat_name}: {', '.join(summaries)}")
            lines.append("")

        # Warnings
        if self.warnings:
            lines.append(f"Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                line = f"  - {w.message}"
                if w.suggestion:
                    line += f" ({w.suggestion})"
                lines.append(line)
            lines.append("")

        # Not imported
        if self.not_imported:
            lines.append(f"Not Imported ({len(self.not_imported)}):")
            for ni in self.not_imported:
                lines.append(f"  - {ni.field}: {ni.reason}")
            lines.append("")

        # Suggestions
        if self.suggestions:
            lines.append("Suggestions:")
            for s in self.suggestions:
                lines.append(f"  - {s}")
            lines.append("")

        return "\n".join(lines).rstrip()


def _categorize_field(field_name: str) -> str:
    """Categorize a field name into a display group.

    Args:
        field_name: The raw field name from the mapper.

    Returns:
        Category label for display grouping.
    """
    identity_fields = {"name", "race", "classes", "background", "alignment"}
    ability_fields = {"abilities"}
    combat_fields = {
        "hit_points_max", "hit_points_current", "temporary_hit_points",
        "armor_class", "speed", "experience_points",
    }
    proficiency_fields = {
        "skill_proficiencies", "saving_throw_proficiencies",
        "tool_proficiencies", "languages",
    }
    spell_fields = {"spells_known", "spell_slots"}
    gear_fields = {"inventory", "equipment"}

    if field_name in identity_fields:
        return "Identity"
    elif field_name in ability_fields:
        return "Abilities"
    elif field_name in combat_fields:
        return "Combat"
    elif field_name in proficiency_fields:
        return "Proficiencies"
    elif field_name in spell_fields:
        return "Spells"
    elif field_name in gear_fields:
        return "Gear"
    else:
        return "Other"


# DDB fields that dm20 does not support (for "not imported" context)
DDB_UNSUPPORTED_FIELDS: dict[str, str] = {
    "character_portrait": "Character portrait/avatar image (not supported)",
    "character_theme": "D&D Beyond visual theme (not applicable)",
    "decorations": "D&D Beyond decorations and badges (not applicable)",
    "campaign_info": "D&D Beyond campaign metadata (use dm20 campaigns instead)",
    "preferences": "D&D Beyond user preferences (not applicable)",
}


class ImportResult(BaseModel):
    """Result of a character import operation."""

    character: Character = Field(description="The created dm20 Character")
    mapped_fields: list[str] = Field(
        default_factory=list,
        description="Field names that were successfully mapped from the source",
    )
    unmapped_fields: list[str] = Field(
        default_factory=list,
        description="Field names that could not be mapped (missing or unsupported)",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues encountered during import (homebrew, unknown items, etc.)",
    )
    source: str = Field(description='Import source: "url" or "file"')
    source_id: int | None = Field(
        default=None,
        description="Original character ID from the source platform (e.g., DDB numeric ID)",
    )

    def build_report(self) -> ImportReport:
        """Build a structured ImportReport from this ImportResult.

        Converts the flat mapped_fields/unmapped_fields/warnings lists into
        structured ImportedField, ImportWarning, NotImported objects with
        contextual summaries and suggestions.

        Returns:
            ImportReport with status, structured fields, warnings, and suggestions.
        """
        char = self.character

        # Build imported fields with value summaries
        imported: list[ImportedField] = []
        for field_name in self.mapped_fields:
            summary = _summarize_field(field_name, char)
            imported.append(ImportedField(name=field_name, summary=summary))

        # Build structured warnings
        structured_warnings: list[ImportWarning] = []
        for warning_text in self.warnings:
            w = _parse_warning(warning_text)
            structured_warnings.append(w)

        # Build not-imported list
        not_imported: list[NotImported] = []
        for field_name in self.unmapped_fields:
            reason = f"Could not map '{field_name}' from source data"
            not_imported.append(NotImported(field=field_name, reason=reason))

        # Add known unsupported DDB fields
        for field_name, reason in DDB_UNSUPPORTED_FIELDS.items():
            not_imported.append(NotImported(field=field_name, reason=reason))

        # Build suggestions
        suggestions = _generate_suggestions(char, self.unmapped_fields, self.warnings)

        # Determine status
        if self.unmapped_fields and not self.mapped_fields:
            status = "failed"
        elif self.warnings or self.unmapped_fields:
            status = "success_with_warnings"
        else:
            status = "success"

        return ImportReport(
            status=status,
            character_name=char.name,
            imported_fields=imported,
            warnings=structured_warnings,
            not_imported=not_imported,
            suggestions=suggestions,
        )


def _summarize_field(field_name: str, char: Character) -> str:
    """Generate a brief summary for an imported field.

    Args:
        field_name: The field name.
        char: The character with the imported data.

    Returns:
        A short human-readable summary string.
    """
    try:
        if field_name == "name":
            return char.name
        elif field_name == "race":
            r = char.race
            return f"{r.subrace + ' ' if r.subrace else ''}{r.name}"
        elif field_name == "classes":
            return " / ".join(
                f"{c.name} {c.level}" + (f" ({c.subclass})" if c.subclass else "")
                for c in char.classes
            )
        elif field_name == "background":
            return char.background or "None"
        elif field_name == "alignment":
            return char.alignment or "None"
        elif field_name == "abilities":
            parts = []
            for ab_name, ab_score in char.abilities.items():
                short = ab_name[:3].upper()
                parts.append(f"{short} {ab_score.score}")
            return ", ".join(parts)
        elif field_name == "hit_points_max":
            return str(char.hit_points_max)
        elif field_name == "hit_points_current":
            return f"{char.hit_points_current}/{char.hit_points_max}"
        elif field_name == "armor_class":
            return str(char.armor_class)
        elif field_name == "speed":
            return f"{char.speed} ft"
        elif field_name == "experience_points":
            return str(char.experience_points)
        elif field_name == "skill_proficiencies":
            return f"{len(char.skill_proficiencies)} skills"
        elif field_name == "saving_throw_proficiencies":
            return ", ".join(char.saving_throw_proficiencies) if char.saving_throw_proficiencies else "None"
        elif field_name == "tool_proficiencies":
            return f"{len(char.tool_proficiencies)} tools"
        elif field_name == "languages":
            return ", ".join(char.languages) if char.languages else "None"
        elif field_name == "inventory":
            return f"{len(char.inventory)} items"
        elif field_name == "equipment":
            equipped = [s for s, v in (char.equipment or {}).items() if v is not None]
            return f"{len(equipped)} slots equipped"
        elif field_name == "spells_known":
            return f"{len(char.spells_known)} spells"
        elif field_name == "spell_slots":
            total = sum(char.spell_slots.values()) if char.spell_slots else 0
            return f"{total} total slots"
        elif field_name == "features":
            return f"{len(char.features)} features"
        elif field_name == "notes":
            return "imported" if char.notes else "empty"
        elif field_name == "temporary_hit_points":
            return str(char.temporary_hit_points)
        else:
            return field_name
    except Exception:
        return field_name


def _parse_warning(warning_text: str) -> ImportWarning:
    """Parse a raw warning string into a structured ImportWarning.

    Args:
        warning_text: The raw warning message string.

    Returns:
        ImportWarning with field, message, and optional suggestion.
    """
    # Try to extract field name from common warning patterns
    field = "general"
    suggestion = ""

    lower = warning_text.lower()

    if "class" in lower:
        field = "classes"
        if "defaulting" in lower:
            suggestion = "Verify character class on D&D Beyond"
    elif "speed" in lower:
        field = "speed"
        suggestion = "Check race speed settings on D&D Beyond"
    elif "inventory" in lower or "item" in lower:
        field = "inventory"
        suggestion = "Re-export character or manually add missing items"
    elif "spell" in lower:
        field = "spells"
        suggestion = "Verify spell list on D&D Beyond"
    elif "feature" in lower or "trait" in lower:
        field = "features"
    elif "homebrew" in lower:
        field = "homebrew"
        suggestion = "Homebrew content imported as custom; verify manually"

    return ImportWarning(
        field=field,
        message=warning_text,
        suggestion=suggestion,
    )


def _generate_suggestions(
    char: Character,
    unmapped: list[str],
    warnings: list[str],
) -> list[str]:
    """Generate actionable suggestions based on the import results.

    Args:
        char: The imported character.
        unmapped: List of fields that could not be mapped.
        warnings: List of warning messages.

    Returns:
        List of suggestion strings.
    """
    suggestions: list[str] = []

    # Suggest loading a rulebook for class validation
    if any("class" in w.lower() for w in warnings):
        suggestions.append(
            "Load a rulebook with 'load_rulebook source=\"srd\"' for class/subclass validation"
        )

    # Suggest manual ability score check if abilities were unmapped
    if "abilities" in unmapped:
        suggestions.append(
            "Ability scores could not be imported. Use 'update_character' to set them manually"
        )

    # Suggest spell slot check for casters
    if char.spells_known and not char.spell_slots:
        suggestions.append(
            "Spells were imported but no spell slots found. "
            "Use 'update_character' to set spell slots manually"
        )

    # Note about player name
    if not char.player_name:
        suggestions.append(
            "No player name assigned. Use 'update_character' to set player_name"
        )

    # Note about missing background
    if not char.background:
        suggestions.append(
            "No background detected. Set it with 'update_character' if needed"
        )

    return suggestions
