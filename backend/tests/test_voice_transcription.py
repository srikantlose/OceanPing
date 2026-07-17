from app.modules.ingest import voice


class _Segment:
    def __init__(self, text):
        self.text = text


class _FakeModel:
    def __init__(self, segments):
        self.segments = segments
        self.calls = 0

    def transcribe(self, audio, beam_size=1):
        self.calls += 1
        return self.segments, {"language": "ta"}


def _reset_voice_module_cache(monkeypatch):
    monkeypatch.setattr(voice, "_model", None)
    monkeypatch.setattr(voice, "_model_failed", False)


def test_transcribe_joins_segments(monkeypatch):
    _reset_voice_module_cache(monkeypatch)
    fake = _FakeModel([_Segment(" kadal thanni "), _Segment("vandhuruchu ")])
    monkeypatch.setattr(voice, "_load_model", lambda: fake)
    assert voice.transcribe(b"fake-ogg-bytes") == "kadal thanni vandhuruchu"


def test_transcribe_returns_none_for_empty_segments(monkeypatch):
    _reset_voice_module_cache(monkeypatch)
    fake = _FakeModel([_Segment("  "), _Segment("")])
    monkeypatch.setattr(voice, "_load_model", lambda: fake)
    assert voice.transcribe(b"fake-ogg-bytes") is None


def test_transcribe_returns_none_when_model_unavailable(monkeypatch):
    _reset_voice_module_cache(monkeypatch)
    monkeypatch.setattr(voice, "_load_model", lambda: None)
    assert voice.transcribe(b"fake-ogg-bytes") is None


def test_transcribe_returns_none_on_decode_error(monkeypatch):
    _reset_voice_module_cache(monkeypatch)

    class _RaisingModel:
        def transcribe(self, audio, beam_size=1):
            raise RuntimeError("bad audio")

    monkeypatch.setattr(voice, "_load_model", lambda: _RaisingModel())
    assert voice.transcribe(b"not-audio") is None


def test_load_model_caches_failure_and_does_not_retry(monkeypatch):
    _reset_voice_module_cache(monkeypatch)
    calls = {"n": 0}

    class _FailingWhisperModel:
        def __init__(self, *a, **kw):
            calls["n"] += 1
            raise OSError("no model weights available offline")

    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        type("_m", (), {"WhisperModel": _FailingWhisperModel}),
    )
    assert voice._load_model() is None
    assert voice._load_model() is None
    assert calls["n"] == 1
