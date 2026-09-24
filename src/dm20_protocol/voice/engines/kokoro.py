"""
Kokoro 82M TTS engine wrapper.

Kokoro is a lightweight, fast TTS model optimized for Apple Silicon.
It serves as the Tier 1 (Speed) engine for Apple Silicon Macs,
providing low-latency synthesis suitable for combat narration.

Requires the `kokoro` package: pip install kokoro
"""

import io
import logging
import struct
import time
from typing import Optional

from .base import AudioFormat, TTSEngine, TTSResult, VoiceConfig

logger = logging.getLogger("dm20-protocol.voice.kokoro")

# Map language codes to Kokoro language identifiers
# Values are Kokoro KPipeline lang_codes: "a" (American English), "b" (British
# English). Only English is mapped here. (Previously the code took the first
# letter of "en-us" -> "e", which is Kokoro's *Spanish* pipeline.)
_LANGUAGE_MAP: dict[str, str] = {
    "en": "a",
    "en-us": "a",
    "en-gb": "b",
}

# Default voice IDs per language
_DEFAULT_VOICES: dict[str, str] = {
    "en": "af_heart",
}


def _check_kokoro_available() -> bool:
    """Check if the kokoro package is importable."""
    try:
        import kokoro  # noqa: F401

        return True
    except ImportError:
        return False


def _audio_to_wav(samples: list[float], sample_rate: int) -> bytes:
    """Convert raw float audio samples to WAV bytes.

    Args:
        samples: Audio samples as floats in [-1.0, 1.0].
        sample_rate: Sample rate in Hz.

    Returns:
        WAV file bytes.
    """
    buf = io.BytesIO()
    num_samples = len(samples)
    data_size = num_samples * 2  # 16-bit = 2 bytes per sample

    # WAV header
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")

    # fmt chunk
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))  # chunk size
    buf.write(struct.pack("<H", 1))  # PCM format
    buf.write(struct.pack("<H", 1))  # mono
    buf.write(struct.pack("<I", sample_rate))
    buf.write(struct.pack("<I", sample_rate * 2))  # byte rate
    buf.write(struct.pack("<H", 2))  # block align
    buf.write(struct.pack("<H", 16))  # bits per sample

    # data chunk
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    for sample in samples:
        clamped = max(-1.0, min(1.0, sample))
        buf.write(struct.pack("<h", int(clamped * 32767)))

    return buf.getvalue()


class KokoroEngine(TTSEngine):
    """Kokoro 82M TTS engine for fast speech synthesis on Apple Silicon.

    This engine wraps the kokoro Python package and provides low-latency
    text-to-speech suitable for real-time combat narration.
    """

    def __init__(self) -> None:
        self._pipelines: dict[str, object] = {}
        self._available: bool | None = None

    @property
    def name(self) -> str:
        return "kokoro"

    def is_available(self) -> bool:
        if self._available is None:
            self._available = _check_kokoro_available()
            if not self._available:
                logger.debug("Kokoro package not installed")
        return self._available

    def _get_pipeline(self, lang_code: str) -> object:
        """Get or create a cached Kokoro pipeline for the given language code."""
        if lang_code not in self._pipelines:
            from kokoro import KPipeline

            self._pipelines[lang_code] = KPipeline(lang_code=lang_code)
            logger.info("Kokoro pipeline loaded for lang_code=%s", lang_code)
        return self._pipelines[lang_code]

    async def warmup(self) -> None:
        """Preload the Kokoro pipeline for faster first synthesis."""
        if not self.is_available():
            return

        if "a" not in self._pipelines:
            try:
                self._get_pipeline("a")
            except Exception as exc:
                logger.warning("Failed to load Kokoro pipeline: %s", exc)
                self._available = False

    async def synthesize(
        self,
        text: str,
        voice_config: Optional[VoiceConfig] = None,
    ) -> TTSResult:
        """Synthesize text using Kokoro 82M.

        Args:
            text: Text to synthesize.
            voice_config: Optional voice configuration.

        Returns:
            TTSResult with WAV audio data.

        Raises:
            RuntimeError: If Kokoro is not available or synthesis fails.
        """
        if not self.is_available():
            raise RuntimeError("Kokoro engine is not available (package not installed)")

        config = voice_config or VoiceConfig()
        lang = config.language
        kokoro_lang = _LANGUAGE_MAP.get(lang, "a")
        voice_id = config.voice_id
        if voice_id == "default":
            voice_id = _DEFAULT_VOICES.get(lang, "af_heart")

        try:
            pipeline = self._get_pipeline(kokoro_lang)

            start_time = time.monotonic()

            # Kokoro generates audio as a generator of (graphemes, phonemes, audio) tuples
            all_samples: list[float] = []
            sample_rate = 24000  # Kokoro default

            for _gs, _ps, audio in pipeline(
                text, voice=voice_id, speed=config.speed
            ):
                if audio is not None:
                    # audio is a numpy/torch tensor or list of floats
                    if hasattr(audio, "tolist"):
                        all_samples.extend(audio.tolist())
                    elif hasattr(audio, "numpy"):
                        all_samples.extend(audio.numpy().tolist())
                    else:
                        all_samples.extend(list(audio))

            elapsed_ms = (time.monotonic() - start_time) * 1000
            duration_ms = (len(all_samples) / sample_rate) * 1000

            wav_data = _audio_to_wav(all_samples, sample_rate)

            logger.info(
                "Kokoro synthesis: %.0fms latency, %.0fms audio duration",
                elapsed_ms,
                duration_ms,
            )

            return TTSResult(
                audio_data=wav_data,
                format=AudioFormat.WAV,
                sample_rate=sample_rate,
                duration_ms=duration_ms,
                engine_name=self.name,
            )

        except ImportError:
            self._available = False
            raise RuntimeError("Kokoro package not found during synthesis")
        except Exception as exc:
            raise RuntimeError(f"Kokoro synthesis failed: {exc}") from exc

    async def shutdown(self) -> None:
        """Release the Kokoro pipeline."""
        self._pipelines.clear()
        logger.debug("Kokoro pipelines released")

    def supported_languages(self) -> list[str]:
        return list(_LANGUAGE_MAP)
