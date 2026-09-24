"""
Tests for individual TTS engine wrappers.

All tests mock the underlying TTS libraries since they are optional
dependencies that may not be installed in the test environment.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dm20_protocol.voice.engines.base import AudioFormat, TTSEngine, TTSResult, VoiceConfig


# ---------------------------------------------------------------------------
# Base class tests
# ---------------------------------------------------------------------------


class TestVoiceConfig:
    """Tests for VoiceConfig dataclass."""

    def test_defaults(self) -> None:
        config = VoiceConfig()
        assert config.voice_id == "default"
        assert config.language == "en"
        assert config.speed == 1.0
        assert config.pitch == 0.0
        assert config.output_format == AudioFormat.WAV
        assert config.extra == {}

    def test_custom_values(self) -> None:
        config = VoiceConfig(
            voice_id="test_voice",
            language="it",
            speed=1.5,
            pitch=-2.0,
            output_format=AudioFormat.MP3,
            extra={"custom": "value"},
        )
        assert config.voice_id == "test_voice"
        assert config.language == "it"
        assert config.speed == 1.5
        assert config.pitch == -2.0
        assert config.extra == {"custom": "value"}


class TestTTSResult:
    """Tests for TTSResult dataclass."""

    def test_creation(self) -> None:
        result = TTSResult(
            audio_data=b"test_audio",
            format=AudioFormat.WAV,
            sample_rate=24000,
            duration_ms=1500.0,
            engine_name="test",
        )
        assert result.audio_data == b"test_audio"
        assert result.format == AudioFormat.WAV
        assert result.sample_rate == 24000
        assert result.duration_ms == 1500.0
        assert result.engine_name == "test"


class TestAudioFormat:
    """Tests for AudioFormat enum."""

    def test_values(self) -> None:
        assert AudioFormat.WAV.value == "wav"
        assert AudioFormat.OPUS.value == "opus"
        assert AudioFormat.MP3.value == "mp3"


# ---------------------------------------------------------------------------
# Kokoro Engine tests
# ---------------------------------------------------------------------------


class TestKokoroEngine:
    """Tests for KokoroEngine with mocked dependencies."""

    def test_name(self) -> None:
        from dm20_protocol.voice.engines.kokoro import KokoroEngine

        engine = KokoroEngine()
        assert engine.name == "kokoro"

    def test_not_available_when_not_installed(self) -> None:
        from dm20_protocol.voice.engines.kokoro import KokoroEngine

        with patch(
            "dm20_protocol.voice.engines.kokoro._check_kokoro_available",
            return_value=False,
        ):
            engine = KokoroEngine()
            assert engine.is_available() is False

    def test_available_when_installed(self) -> None:
        from dm20_protocol.voice.engines.kokoro import KokoroEngine

        with patch(
            "dm20_protocol.voice.engines.kokoro._check_kokoro_available",
            return_value=True,
        ):
            engine = KokoroEngine()
            assert engine.is_available() is True

    @pytest.mark.asyncio
    async def test_synthesize_raises_when_not_available(self) -> None:
        from dm20_protocol.voice.engines.kokoro import KokoroEngine

        with patch(
            "dm20_protocol.voice.engines.kokoro._check_kokoro_available",
            return_value=False,
        ):
            engine = KokoroEngine()
            with pytest.raises(RuntimeError, match="not available"):
                await engine.synthesize("test")

    @pytest.mark.asyncio
    async def test_synthesize_with_mocked_pipeline(self) -> None:
        from dm20_protocol.voice.engines.kokoro import KokoroEngine

        mock_audio = MagicMock()
        mock_audio.tolist.return_value = [0.1, 0.2, -0.3, 0.4] * 100

        mock_pipeline = MagicMock()
        mock_pipeline.return_value = [("gs", "ps", mock_audio)]

        with patch(
            "dm20_protocol.voice.engines.kokoro._check_kokoro_available",
            return_value=True,
        ):
            engine = KokoroEngine()
            engine._pipelines["a"] = mock_pipeline  # Kokoro lang_code "a" = American English

            result = await engine.synthesize("Hello world")

            assert isinstance(result, TTSResult)
            assert result.engine_name == "kokoro"
            assert result.format == AudioFormat.WAV
            assert len(result.audio_data) > 0
            # Check WAV header
            assert result.audio_data[:4] == b"RIFF"
            assert result.audio_data[8:12] == b"WAVE"

    @pytest.mark.asyncio
    async def test_synthesize_with_voice_config(self) -> None:
        from dm20_protocol.voice.engines.kokoro import KokoroEngine

        mock_audio = MagicMock()
        mock_audio.tolist.return_value = [0.0] * 100

        mock_pipeline = MagicMock()
        mock_pipeline.return_value = [("gs", "ps", mock_audio)]

        with patch(
            "dm20_protocol.voice.engines.kokoro._check_kokoro_available",
            return_value=True,
        ):
            engine = KokoroEngine()
            engine._pipelines["a"] = mock_pipeline  # Kokoro lang_code "a" = American English

            config = VoiceConfig(language="it", speed=1.2)
            result = await engine.synthesize("Ciao mondo", config)

            assert result.engine_name == "kokoro"
            mock_pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_clears_pipeline(self) -> None:
        from dm20_protocol.voice.engines.kokoro import KokoroEngine

        engine = KokoroEngine()
        engine._pipelines["a"] = MagicMock()

        await engine.shutdown()
        assert engine._pipelines == {}

    def test_supported_languages(self) -> None:
        from dm20_protocol.voice.engines.kokoro import KokoroEngine

        engine = KokoroEngine()
        langs = engine.supported_languages()
        assert "en" in langs
        assert "it" not in langs  # Kokoro mapping is English-only here


# ---------------------------------------------------------------------------
# Qwen3-TTS Engine tests
# ---------------------------------------------------------------------------


class TestQwen3TTSEngine:
    """Tests for Qwen3TTSEngine with mocked dependencies."""

    def test_name(self) -> None:
        from dm20_protocol.voice.engines.qwen3 import Qwen3TTSEngine

        engine = Qwen3TTSEngine()
        assert engine.name == "qwen3-tts"

    def test_not_available_when_not_installed(self) -> None:
        from dm20_protocol.voice.engines.qwen3 import Qwen3TTSEngine

        with patch(
            "dm20_protocol.voice.engines.qwen3._check_mlx_audio_available",
            return_value=False,
        ):
            engine = Qwen3TTSEngine()
            assert engine.is_available() is False

    def test_available_when_installed(self) -> None:
        from dm20_protocol.voice.engines.qwen3 import Qwen3TTSEngine

        with patch(
            "dm20_protocol.voice.engines.qwen3._check_mlx_audio_available",
            return_value=True,
        ):
            engine = Qwen3TTSEngine()
            assert engine.is_available() is True

    @pytest.mark.asyncio
    async def test_synthesize_raises_when_not_available(self) -> None:
        from dm20_protocol.voice.engines.qwen3 import Qwen3TTSEngine

        with patch(
            "dm20_protocol.voice.engines.qwen3._check_mlx_audio_available",
            return_value=False,
        ):
            engine = Qwen3TTSEngine()
            with pytest.raises(RuntimeError, match="not available"):
                await engine.synthesize("test")

    @pytest.mark.asyncio
    async def test_synthesize_with_mocked_tts(self) -> None:
        from dm20_protocol.voice.engines.qwen3 import Qwen3TTSEngine

        mock_result = MagicMock()
        mock_result.tolist.return_value = [0.1, -0.2, 0.3] * 100

        mock_tts = MagicMock()
        mock_tts.generate.return_value = mock_result

        with patch(
            "dm20_protocol.voice.engines.qwen3._check_mlx_audio_available",
            return_value=True,
        ):
            engine = Qwen3TTSEngine()
            engine._tts = mock_tts

            result = await engine.synthesize("Hello")

            assert isinstance(result, TTSResult)
            assert result.engine_name == "qwen3-tts"
            assert result.format == AudioFormat.WAV
            assert result.audio_data[:4] == b"RIFF"

    @pytest.mark.asyncio
    async def test_shutdown_clears_model(self) -> None:
        from dm20_protocol.voice.engines.qwen3 import Qwen3TTSEngine

        engine = Qwen3TTSEngine()
        engine._tts = MagicMock()

        await engine.shutdown()
        assert engine._tts is None

    def test_supported_languages(self) -> None:
        from dm20_protocol.voice.engines.qwen3 import Qwen3TTSEngine

        engine = Qwen3TTSEngine()
        langs = engine.supported_languages()
        assert "en" in langs
        assert "it" in langs
        assert "zh" in langs


# ---------------------------------------------------------------------------
# Edge-TTS Engine tests
# ---------------------------------------------------------------------------


class TestEdgeTTSEngine:
    """Tests for EdgeTTSEngine with mocked dependencies."""

    def test_name(self) -> None:
        from dm20_protocol.voice.engines.edge_tts import EdgeTTSEngine

        engine = EdgeTTSEngine()
        assert engine.name == "edge-tts"

    def test_not_available_when_not_installed(self) -> None:
        from dm20_protocol.voice.engines.edge_tts import EdgeTTSEngine

        with patch(
            "dm20_protocol.voice.engines.edge_tts._check_edge_tts_available",
            return_value=False,
        ):
            engine = EdgeTTSEngine()
            assert engine.is_available() is False

    def test_available_when_installed(self) -> None:
        from dm20_protocol.voice.engines.edge_tts import EdgeTTSEngine

        with patch(
            "dm20_protocol.voice.engines.edge_tts._check_edge_tts_available",
            return_value=True,
        ):
            engine = EdgeTTSEngine()
            assert engine.is_available() is True

    @pytest.mark.asyncio
    async def test_synthesize_raises_when_not_available(self) -> None:
        from dm20_protocol.voice.engines.edge_tts import EdgeTTSEngine

        with patch(
            "dm20_protocol.voice.engines.edge_tts._check_edge_tts_available",
            return_value=False,
        ):
            engine = EdgeTTSEngine()
            with pytest.raises(RuntimeError, match="not available"):
                await engine.synthesize("test")

    @pytest.mark.asyncio
    async def test_synthesize_with_mocked_edge_tts(self) -> None:
        from dm20_protocol.voice.engines.edge_tts import EdgeTTSEngine

        async def mock_stream():
            yield {"type": "audio", "data": b"\x00\x01\x02"}
            yield {"type": "audio", "data": b"\x03\x04\x05"}
            yield {"type": "WordBoundary", "data": "test"}

        mock_communicate = MagicMock()
        mock_communicate.stream = mock_stream

        mock_edge_module = MagicMock()
        mock_edge_module.Communicate.return_value = mock_communicate

        with patch(
            "dm20_protocol.voice.engines.edge_tts._check_edge_tts_available",
            return_value=True,
        ), patch.dict(sys.modules, {"edge_tts": mock_edge_module}):
            engine = EdgeTTSEngine()
            engine._available = True
            result = await engine.synthesize("Hello")

            assert isinstance(result, TTSResult)
            assert result.engine_name == "edge-tts"
            assert result.format == AudioFormat.MP3
            assert result.audio_data == b"\x00\x01\x02\x03\x04\x05"

    @pytest.mark.asyncio
    async def test_synthesize_italian_voice(self) -> None:
        from dm20_protocol.voice.engines.edge_tts import EdgeTTSEngine

        async def mock_stream():
            yield {"type": "audio", "data": b"\x00"}

        mock_communicate = MagicMock()
        mock_communicate.stream = mock_stream

        mock_edge_module = MagicMock()
        mock_edge_module.Communicate.return_value = mock_communicate

        with patch(
            "dm20_protocol.voice.engines.edge_tts._check_edge_tts_available",
            return_value=True,
        ), patch.dict(sys.modules, {"edge_tts": mock_edge_module}):
            engine = EdgeTTSEngine()
            engine._available = True
            config = VoiceConfig(language="it")
            result = await engine.synthesize("Ciao", config)

            # Verify Italian voice was selected
            call_args = mock_edge_module.Communicate.call_args
            assert "it-IT-DiegoNeural" in str(call_args)

    def test_supported_languages_extensive(self) -> None:
        from dm20_protocol.voice.engines.edge_tts import EdgeTTSEngine

        engine = EdgeTTSEngine()
        langs = engine.supported_languages()
        assert "en" in langs
        assert "it" in langs
        assert "de" in langs
        assert "fr" in langs


# ---------------------------------------------------------------------------
# Piper Engine tests
# ---------------------------------------------------------------------------


class TestPiperEngine:
    """Tests for PiperEngine with mocked dependencies."""

    def test_name(self) -> None:
        from dm20_protocol.voice.engines.piper import PiperEngine

        engine = PiperEngine()
        assert engine.name == "piper"

    def test_not_available_when_not_installed(self) -> None:
        from dm20_protocol.voice.engines.piper import PiperEngine

        with patch(
            "dm20_protocol.voice.engines.piper._check_piper_available",
            return_value=False,
        ):
            engine = PiperEngine()
            assert engine.is_available() is False

    def test_available_when_installed(self) -> None:
        from dm20_protocol.voice.engines.piper import PiperEngine

        with patch(
            "dm20_protocol.voice.engines.piper._check_piper_available",
            return_value=True,
        ):
            engine = PiperEngine()
            assert engine.is_available() is True

    @pytest.mark.asyncio
    async def test_synthesize_raises_when_not_available(self) -> None:
        from dm20_protocol.voice.engines.piper import PiperEngine

        with patch(
            "dm20_protocol.voice.engines.piper._check_piper_available",
            return_value=False,
        ):
            engine = PiperEngine()
            with pytest.raises(RuntimeError, match="not available"):
                await engine.synthesize("test")

    @pytest.mark.asyncio
    async def test_synthesize_with_mocked_piper(self) -> None:
        from dm20_protocol.voice.engines.piper import PiperEngine

        # Piper synthesize_stream_raw yields raw 16-bit PCM chunks
        raw_pcm = b"\x00\x01" * 100  # 100 samples of 16-bit audio

        mock_voice = MagicMock()
        mock_voice.synthesize_stream_raw.return_value = [raw_pcm]

        with patch(
            "dm20_protocol.voice.engines.piper._check_piper_available",
            return_value=True,
        ):
            engine = PiperEngine()
            engine._voice = mock_voice
            engine._current_model = "en_US-lessac-medium"
            engine._available = True

            result = await engine.synthesize("Hello")

            assert isinstance(result, TTSResult)
            assert result.engine_name == "piper"
            assert result.format == AudioFormat.WAV
            assert result.audio_data[:4] == b"RIFF"

    @pytest.mark.asyncio
    async def test_shutdown_clears_voice(self) -> None:
        from dm20_protocol.voice.engines.piper import PiperEngine

        engine = PiperEngine()
        engine._voice = MagicMock()
        engine._current_model = "test"

        await engine.shutdown()
        assert engine._voice is None
        assert engine._current_model is None

    def test_supported_languages(self) -> None:
        from dm20_protocol.voice.engines.piper import PiperEngine

        engine = PiperEngine()
        langs = engine.supported_languages()
        assert "en" in langs
        assert "it" in langs
