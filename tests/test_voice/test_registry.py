"""Tests for VoiceRegistry — YAML loading, cascade, overrides."""

import pytest
import yaml

from dm20_protocol.voice.engines.base import AudioFormat, VoiceConfig
from dm20_protocol.voice.registry import VoiceRegistry, _raw_to_voice_config


# ── Helpers ──────────────────────────────────────────────────────────

def _write_registry(tmp_path, data: dict):
    """Write a voice_registry.yaml into a tmp campaign dir."""
    path = tmp_path / "voice_registry.yaml"
    with open(path, "w") as fh:
        yaml.safe_dump(data, fh, default_flow_style=False)
    return tmp_path


# ── _raw_to_voice_config ────────────────────────────────────────────

class TestRawToVoiceConfig:
    def test_minimal(self):
        cfg = _raw_to_voice_config({})
        assert cfg.voice_id == "default"
        assert cfg.language == "en"
        assert cfg.speed == 1.0
        assert cfg.output_format == AudioFormat.WAV

    def test_custom_fields(self):
        raw = {
            "voice_id": "af_heart",
            "language": "it",
            "speed": 1.2,
            "pitch": -0.5,
            "output_format": "opus",
            "engine": "kokoro",
            "voice_design": "A calm voice",
        }
        cfg = _raw_to_voice_config(raw)
        assert cfg.voice_id == "af_heart"
        assert cfg.language == "it"
        assert cfg.speed == 1.2
        assert cfg.pitch == -0.5
        assert cfg.output_format == AudioFormat.OPUS
        assert cfg.extra["engine"] == "kokoro"
        assert cfg.extra["voice_design"] == "A calm voice"

    def test_voice_alias(self):
        """'voice' key is treated as alias for 'voice_id'."""
        cfg = _raw_to_voice_config({"voice": "my_voice"})
        assert cfg.voice_id == "my_voice"

    def test_invalid_format_falls_back(self):
        cfg = _raw_to_voice_config({"output_format": "flac"})
        assert cfg.output_format == AudioFormat.WAV

    def test_default_language_override(self):
        cfg = _raw_to_voice_config({}, default_language="it")
        assert cfg.language == "it"


# ── VoiceRegistry creation ──────────────────────────────────────────

class TestRegistryCreation:
    def test_creates_default_file(self, tmp_path):
        """First run creates voice_registry.yaml with defaults."""
        reg = VoiceRegistry(tmp_path)
        assert reg.path.exists()
        data = yaml.safe_load(reg.path.read_text())
        assert data["version"] == 1
        assert "dm_voice" in data
        assert "combat_voice" in data

    def test_loads_existing_file(self, tmp_path):
        data = {
            "version": 1,
            "default_language": "it",
            "dm_voice": {"engine": "qwen3-tts", "voice_design": "Test"},
            "combat_voice": {"engine": "kokoro"},
            "npc_defaults": {},
            "npc_overrides": {},
        }
        _write_registry(tmp_path, data)
        reg = VoiceRegistry(tmp_path)
        assert reg.default_language == "it"
        dm = reg.get_dm_voice()
        assert dm.extra["engine"] == "qwen3-tts"

    def test_invalid_yaml_uses_defaults(self, tmp_path):
        (tmp_path / "voice_registry.yaml").write_text("not a dict")
        reg = VoiceRegistry(tmp_path)
        assert reg.config["version"] == 1


# ── Role voices ─────────────────────────────────────────────────────

class TestRoleVoices:
    def test_get_dm_voice(self, tmp_path):
        reg = VoiceRegistry(tmp_path)
        cfg = reg.get_dm_voice()
        assert isinstance(cfg, VoiceConfig)

    def test_get_combat_voice(self, tmp_path):
        reg = VoiceRegistry(tmp_path)
        cfg = reg.get_combat_voice()
        assert isinstance(cfg, VoiceConfig)

    def test_get_voice_config_dm(self, tmp_path):
        reg = VoiceRegistry(tmp_path)
        cfg = reg.get_voice_config("dm")
        dm = reg.get_dm_voice()
        assert cfg.voice_id == dm.voice_id

    def test_get_voice_config_combat(self, tmp_path):
        reg = VoiceRegistry(tmp_path)
        cfg = reg.get_voice_config("combat")
        combat = reg.get_combat_voice()
        assert cfg.voice_id == combat.voice_id


# ── NPC cascade ─────────────────────────────────────────────────────

class TestNPCCascade:
    @pytest.fixture
    def registry(self, tmp_path):
        data = {
            "version": 1,
            "default_language": "en",
            "dm_voice": {"engine": "edge-tts", "voice_id": "dm_default"},
            "combat_voice": {"engine": "edge-tts"},
            "npc_defaults": {
                "male_human": {"engine": "qwen3-tts", "voice_id": "male_human_v"},
                "female_elf": {"engine": "kokoro", "voice_id": "female_elf_v"},
                "male_*": {"engine": "edge-tts", "voice_id": "male_wildcard_v"},
                "*_dwarf": {"engine": "edge-tts", "voice_id": "dwarf_wildcard_v"},
            },
            "npc_overrides": {
                "giuseppe_barkeep": {
                    "engine": "qwen3-tts",
                    "voice_design": "Italian man",
                    "language": "it",
                    "voice_id": "giuseppe_v",
                },
            },
        }
        _write_registry(tmp_path, data)
        return VoiceRegistry(tmp_path)

    def test_exact_override(self, registry):
        cfg = registry.get_npc_voice("giuseppe_barkeep")
        assert cfg.voice_id == "giuseppe_v"
        assert cfg.language == "it"

    def test_exact_archetype(self, registry):
        cfg = registry.get_npc_voice("random_npc", race="human", gender="male")
        assert cfg.voice_id == "male_human_v"

    def test_gender_wildcard(self, registry):
        cfg = registry.get_npc_voice("some_orc", race="orc", gender="male")
        assert cfg.voice_id == "male_wildcard_v"

    def test_race_wildcard(self, registry):
        cfg = registry.get_npc_voice("lady_dwarf", race="dwarf", gender="female")
        # female_dwarf doesn't exist, female_* doesn't exist, but *_dwarf does
        assert cfg.voice_id == "dwarf_wildcard_v"

    def test_no_match_falls_to_dm(self, registry):
        cfg = registry.get_npc_voice("unknown")
        assert cfg.voice_id == "dm_default"

    def test_case_insensitive(self, registry):
        cfg = registry.get_npc_voice("Giuseppe_Barkeep")
        assert cfg.voice_id == "giuseppe_v"

    def test_override_via_get_voice_config(self, registry):
        """get_voice_config also resolves NPC overrides."""
        cfg = registry.get_voice_config("giuseppe_barkeep")
        assert cfg.voice_id == "giuseppe_v"


# ── Mutation methods ────────────────────────────────────────────────

class TestRegistryMutation:
    def test_set_npc_voice(self, tmp_path):
        reg = VoiceRegistry(tmp_path)
        reg.set_npc_voice("thorin", {"engine": "kokoro", "voice_id": "thorin_v"})

        cfg = reg.get_npc_voice("thorin")
        assert cfg.voice_id == "thorin_v"

        # Persisted to disk
        reg2 = VoiceRegistry(tmp_path)
        assert reg2.get_npc_voice("thorin").voice_id == "thorin_v"

    def test_remove_npc_voice(self, tmp_path):
        reg = VoiceRegistry(tmp_path)
        reg.set_npc_voice("thorin", {"voice_id": "thorin_v"})
        assert reg.remove_npc_voice("thorin") is True
        assert reg.remove_npc_voice("thorin") is False

    def test_set_archetype_default(self, tmp_path):
        reg = VoiceRegistry(tmp_path)
        reg.set_archetype_default("female_*", {"voice_id": "generic_female"})
        # race without its own built-in archetype (tiefling now has one), so female_* applies
        cfg = reg.get_npc_voice("lady", gender="female", race="genasi")
        assert cfg.voice_id == "generic_female"

    def test_list_overrides(self, tmp_path):
        reg = VoiceRegistry(tmp_path)
        reg.set_npc_voice("a", {"voice_id": "va"})
        reg.set_npc_voice("b", {"voice_id": "vb"})
        overrides = reg.list_overrides()
        assert "a" in overrides
        assert "b" in overrides

    def test_list_archetypes(self, tmp_path):
        reg = VoiceRegistry(tmp_path)
        archetypes = reg.list_archetypes()
        assert "male_human" in archetypes
