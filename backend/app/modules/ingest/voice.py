"""Voice-note transcription: Telegram voice messages go through the same
create_report(text=...) path as typed descriptions once faster-whisper turns
them into text. CPU-only, lazy-loaded (mirrors nlp/classifier.py's pattern)
so importing this module costs nothing until the first voice note arrives.
"""
import io
import logging
import threading

from app.core.config import get_settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_model = None
_model_failed = False


def _load_model():
    global _model, _model_failed
    if _model is not None or _model_failed:
        return _model
    with _lock:
        if _model is not None or _model_failed:
            return _model
        try:
            from faster_whisper import WhisperModel

            settings = get_settings()
            _model = WhisperModel(
                settings.whisper_model_size,
                device=settings.whisper_device,
                compute_type=settings.whisper_compute_type,
            )
            log.info("Whisper model loaded: %s", settings.whisper_model_size)
        except Exception:
            log.exception("Whisper model unavailable; voice notes will be skipped")
            _model_failed = True
    return _model


def transcribe(audio_bytes: bytes) -> str | None:
    """Best-effort: returns None (never raises) if the model can't load or
    decoding fails, so a bad voice note degrades to a photo-only report
    instead of breaking the bot conversation."""
    model = _load_model()
    if model is None:
        return None
    try:
        segments, _info = model.transcribe(io.BytesIO(audio_bytes), beam_size=1)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text or None
    except Exception:
        log.exception("Voice transcription failed")
        return None
