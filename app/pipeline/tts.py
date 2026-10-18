"""Gemini expressive text-to-speech.

Narration uses Google Gemini 3.8 Live (https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live)
for human-like expressive delivery, conversational rhythm, authentic pauses,
and vocal warmth. Falls back gracefully to Gemini Flash TTS if the live
stream is temporarily unreachable.
"""
from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import io
import random
import re
import time
import wave
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..logging_setup import log
from . import ratelimit

# Gemini 3.8 Live is the primary voice engine for human-like narration.
# Fallbacks for accounts where Live API streaming is restricted.
_TTS_MODEL_FALLBACKS = (
    "gemini-3.8-live",
    "gemini-3.8-flash-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
)

# Human-like expressive instruction for Gemini 3.8 Live
_EXPRESSIVE_LIVE_INSTRUCTION = (
    "You are an expressive, human-like voice narrator for high-production educational videos. "
    "Speak with rich human-like expression, authentic vocal warmth, conversational rhythm, "
    "natural breath and pacing, and subtle pitch inflections that bring ideas to life. "
    "Vary your cadence dynamically: sound genuinely intrigued when introducing questions, "
    "vibrant and clear when explaining breakthroughs, and confident on conclusions. "
    "Do NOT speak in a flat or robotic monotone. "
    "Speak the provided narration script faithfully with genuine personality, warmth, and expressive flair."
)

# Standard TTS delivery style for fallback models
_NARRATION_STYLE = (
    "Clear educational narration with natural human pacing, warmth, and expressive precision."
)

# Native Gemini Live prebuilt voices (8 expressive personas)
PRIMARY_LIVE_VOICES = {
    "kore": "Kore",
    "aoede": "Aoede",
    "charon": "Charon",
    "puck": "Puck",
    "fenrir": "Fenrir",
    "leda": "Leda",
    "orus": "Orus",
    "zephyr": "Zephyr",
}

# 30 prebuilt studio voices with the 8 Gemini 3.8 Live voices featured
GEMINI_VOICES: list[dict] = [
    {"id": "Kore", "label": "Kore", "gender": "female", "vibe": "Firm · Expressive", "tag": "Recommended", "hue": 262},
    {"id": "Aoede", "label": "Aoede", "gender": "female", "vibe": "Breezy · Warm", "tag": "Popular", "hue": 190},
    {"id": "Charon", "label": "Charon", "gender": "male", "vibe": "Informative · Deep", "tag": "Explainers", "hue": 215},
    {"id": "Puck", "label": "Puck", "gender": "male", "vibe": "Upbeat · Dynamic", "tag": "Engaging", "hue": 45},
    {"id": "Zephyr", "label": "Zephyr", "gender": "female", "vibe": "Bright · Crisp", "tag": "Live", "hue": 160},
    {"id": "Leda", "label": "Leda", "gender": "female", "vibe": "Youthful · Clear", "tag": "Live", "hue": 330},
    {"id": "Fenrir", "label": "Fenrir", "gender": "male", "vibe": "Excitable · Fast", "tag": "Live", "hue": 0},
    {"id": "Orus", "label": "Orus", "gender": "male", "vibe": "Firm · Confident", "tag": "Live", "hue": 250},
    {"id": "Callirrhoe", "label": "Callirrhoe", "gender": "female", "vibe": "Easy-going", "tag": "", "hue": 280},
    {"id": "Autonoe", "label": "Autonoe", "gender": "female", "vibe": "Bright", "tag": "", "hue": 140},
    {"id": "Sulafat", "label": "Sulafat", "gender": "female", "vibe": "Warm", "tag": "", "hue": 20},
    {"id": "Achernar", "label": "Achernar", "gender": "female", "vibe": "Soft", "tag": "", "hue": 310},
    {"id": "Despina", "label": "Despina", "gender": "female", "vibe": "Smooth", "tag": "", "hue": 200},
    {"id": "Erinome", "label": "Erinome", "gender": "female", "vibe": "Clear", "tag": "", "hue": 175},
    {"id": "Gacrux", "label": "Gacrux", "gender": "female", "vibe": "Mature", "tag": "", "hue": 300},
    {"id": "Laomedeia", "label": "Laomedeia", "gender": "female", "vibe": "Upbeat", "tag": "", "hue": 50},
    {"id": "Pulcherrima", "label": "Pulcherrima", "gender": "female", "vibe": "Forward", "tag": "", "hue": 340},
    {"id": "Vindemiatrix", "label": "Vindemiatrix", "gender": "female", "vibe": "Gentle", "tag": "", "hue": 150},
    {"id": "Achird", "label": "Achird", "gender": "male", "vibe": "Friendly", "tag": "", "hue": 30},
    {"id": "Algenib", "label": "Algenib", "gender": "male", "vibe": "Gravelly", "tag": "", "hue": 10},
    {"id": "Algieba", "label": "Algieba", "gender": "male", "vibe": "Smooth", "tag": "", "hue": 220},
    {"id": "Alnilam", "label": "Alnilam", "gender": "male", "vibe": "Firm", "tag": "", "hue": 240},
    {"id": "Enceladus", "label": "Enceladus", "gender": "male", "vibe": "Breathy", "tag": "", "hue": 180},
    {"id": "Iapetus", "label": "Iapetus", "gender": "male", "vibe": "Clear", "tag": "", "hue": 195},
    {"id": "Rasalgethi", "label": "Rasalgethi", "gender": "male", "vibe": "Informative", "tag": "", "hue": 205},
    {"id": "Sadachbia", "label": "Sadachbia", "gender": "male", "vibe": "Lively", "tag": "", "hue": 55},
    {"id": "Sadaltager", "label": "Sadaltager", "gender": "male", "vibe": "Knowledgeable", "tag": "", "hue": 230},
    {"id": "Schedar", "label": "Schedar", "gender": "male", "vibe": "Even", "tag": "", "hue": 210},
    {"id": "Umbriel", "label": "Umbriel", "gender": "male", "vibe": "Easy-going", "tag": "", "hue": 170},
    {"id": "Zubenelgenubi", "label": "Zubenelgenubi", "gender": "male", "vibe": "Casual", "tag": "", "hue": 25},
]

_VOICE_BY_ID = {v["id"].lower(): v["id"] for v in GEMINI_VOICES}
_VOICE_GENDER = {v["id"].lower(): v["gender"] for v in GEMINI_VOICES}


def _has_module(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def gemini_tts_available() -> bool:
    """True when a Gemini key and the GenAI SDK are present."""
    return bool((get_settings().gemini_api_key or "").strip()) and _has_module("google.genai")


def selected_engine() -> str:
    return "gemini" if gemini_tts_available() else "none"


def list_gemini_voices() -> list[dict]:
    return [dict(v) for v in GEMINI_VOICES]


def gemini_voice_ids() -> set[str]:
    return {v["id"] for v in GEMINI_VOICES}


def default_voice() -> str:
    raw = (get_settings().gemini_tts_voice or "Kore").strip() or "Kore"
    return resolve_voice(raw)


def resolve_voice(name: str | None) -> str:
    """Return the canonical prebuilt voice id, or raise ValueError."""
    key = (name or "").strip()
    if not key:
        key = (get_settings().gemini_tts_voice or "Kore").strip() or "Kore"
    found = _VOICE_BY_ID.get(key.lower())
    if not found:
        raise ValueError(
            f"Unknown Gemini voice: {key}. Pick one of the studio voices."
        )
    return found


def resolve_live_voice(name: str | None) -> str:
    """Map any studio voice to an expressive Gemini 3.8 Live prebuilt voice."""
    key = (name or "").strip().lower()
    if key in PRIMARY_LIVE_VOICES:
        return PRIMARY_LIVE_VOICES[key]
    gender = _VOICE_GENDER.get(key, "female")
    return "Kore" if gender == "female" else "Charon"


def voice_from_options(options: dict | None) -> str:
    """Voice for a job. Legacy Kokoro / Pocket ids are ignored."""
    opts = options or {}
    raw = (opts.get("gemini_voice") or "").strip()
    if raw:
        return resolve_voice(raw)
    for key in ("voice_preset", "pocket_voice"):
        legacy = (opts.get(key) or "").strip()
        if legacy.lower() in _VOICE_BY_ID:
            return resolve_voice(legacy)
    return default_voice()


def output_suffix() -> str:
    return ".wav"


def _model_chain() -> list[str]:
    primary = (get_settings().gemini_tts_model or "").strip() or _TTS_MODEL_FALLBACKS[0]
    chain = [primary]
    for mid in _TTS_MODEL_FALLBACKS:
        if mid not in chain:
            chain.append(mid)
    return chain


def _chunks(text: str, limit: int = 3500) -> list[str]:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ["..."]
    if len(cleaned) <= limit:
        return [cleaned]
    parts: list[str] = []
    buf = ""
    for sent in re.split(r"(?<=[.!?])\s+", cleaned):
        if not sent:
            continue
        if buf and len(buf) + 1 + len(sent) > limit:
            parts.append(buf)
            buf = sent
        else:
            buf = f"{buf} {sent}".strip()
        while len(buf) > limit:
            parts.append(buf[:limit].strip())
            buf = buf[limit:].strip()
    if buf:
        parts.append(buf)
    return parts or ["..."]


def _pcm_from_audio(data: bytes) -> tuple[bytes, int]:
    """Return (pcm16, sample_rate). Accept a WAV container or raw PCM."""
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data), "rb") as wf:
            rate = int(wf.getframerate() or 24000)
            return wf.readframes(wf.getnframes()), rate
    return data, 24000


def _write_wav(path: Path, pcm: bytes, rate: int = 24000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 16-bit frames must be an even number of bytes or wave raises.
    if len(pcm) % 2:
        pcm += b"\x00"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate or 24000)
        wf.writeframes(pcm)


def _run_coroutine_safely(coro: Any) -> Any:
    """Run an async coroutine safely from synchronous or running-loop contexts."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


async def _generate_live_async(client: Any, model: str, text: str, live_voice: str) -> tuple[bytes, int]:
    """Generate expressive human-like narration using Gemini 3.8 Live."""
    from google.genai import types

    cfg = {
        "response_modalities": ["AUDIO"],
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {"voice_name": live_voice}
            }
        },
        "system_instruction": _EXPRESSIVE_LIVE_INSTRUCTION,
    }
    async with client.aio.live.connect(model=model, config=cfg) as session:
        prompt_text = (
            f"Please speak this narration script with rich human-like expression, natural vocal warmth, "
            f"and conversational cadence:\n\n{text}"
        )
        content = types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt_text)],
        )
        await session.send_client_content(turns=[content], turn_complete=True)
        chunks: list[bytes] = []
        async for resp in session.receive():
            sc = getattr(resp, "server_content", None)
            if sc:
                mt = getattr(sc, "model_turn", None)
                if mt:
                    for part in getattr(mt, "parts", []):
                        inline = getattr(part, "inline_data", None)
                        if inline is not None and getattr(inline, "data", None):
                            chunks.append(inline.data)
                if getattr(sc, "turn_complete", False):
                    break
        if not chunks:
            raise RuntimeError(f"{model} returned empty audio chunks")
        return b"".join(chunks), 24000


def _speech_config_fallback(voice: str):
    from google.genai import types

    try:
        return types.SpeechConfig(
            voice_config=types.VoiceConfig(voice=voice),
        )
    except Exception:
        return types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice),
            ),
        )


def _contents_fallback(text: str):
    from google.genai import types

    part_kwargs: dict = {"text": text}
    try:
        meta_cls = getattr(types, "SpeechMetadata", None)
        if meta_cls is not None:
            part_kwargs["speech_metadata"] = meta_cls(style=_NARRATION_STYLE)
    except Exception:
        pass
    try:
        return [
            types.Content(
                role="user",
                parts=[types.Part(**part_kwargs)],
            )
        ]
    except Exception:
        return text


def _audio_from_response(response: Any) -> tuple[bytes, int]:
    candidates = getattr(response, "candidates", None) or []
    parts = []
    if candidates and getattr(candidates[0], "content", None) is not None:
        parts = getattr(candidates[0].content, "parts", None) or []
    blobs: list[bytes] = []
    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline is None:
            continue
        data = getattr(inline, "data", None)
        if not data:
            continue
        if isinstance(data, str):
            data = base64.b64decode(data)
        blobs.append(bytes(data))
    if not blobs:
        raise RuntimeError("Gemini TTS returned no audio")
    pcm_parts: list[bytes] = []
    rate = 24000
    for blob in blobs:
        pcm, blob_rate = _pcm_from_audio(blob)
        if pcm:
            pcm_parts.append(pcm)
            rate = blob_rate or rate
    if not pcm_parts:
        raise RuntimeError("Gemini TTS audio was empty")
    return b"".join(pcm_parts), rate


def _generate_fallback_once(client: Any, model: str, text: str, voice: str) -> tuple[bytes, int]:
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=_contents_fallback(text),
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=_speech_config_fallback(voice),
        ),
    )
    return _audio_from_response(response)


def _model_missing(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in ("not found", "not supported", "unknown model", "404", "invalid model", "deprecated")
    )


def synthesize(text: str, out_path: Path, voice: str | None = None) -> str:
    """Write human-like narration WAV for ``text``.

    Uses Gemini 3.8 Live for authentic human expression, with automatic fallback
    to Gemini Flash TTS. Returns ``\"gemini\"``.
    """
    if not gemini_tts_available():
        raise RuntimeError("Gemini voice narration needs GEMINI_API_KEY and the google-genai package.")
    voice_id = resolve_voice(voice)
    live_voice = resolve_live_voice(voice_id)
    out_path = Path(out_path)
    if out_path.suffix.lower() != ".wav":
        out_path = out_path.with_suffix(".wav")

    from google import genai

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)
    pieces = _chunks(text)
    attempts = max(1, int(settings.llm_max_retries or 1))
    base = float(settings.llm_retry_base_delay or 1.5)
    models = _model_chain()
    last_exc: Exception | None = None
    pcm_all = b""
    rate = 24000
    used_model = models[0]

    for piece in pieces:
        spoken = False
        for model in models:
            i = 0
            max_tries = attempts
            while i < max_tries:
                ratelimit.throttle()
                try:
                    if "live" in model.lower():
                        pcm, rate = _run_coroutine_safely(
                            _generate_live_async(client, model, piece, live_voice)
                        )
                    else:
                        pcm, rate = _generate_fallback_once(client, model, piece, voice_id)

                    pcm_all += pcm
                    used_model = model
                    spoken = True
                    break
                except Exception as e:  # noqa: BLE001
                    last_exc = e
                    if _model_missing(e):
                        log.bind(task="TTS").warning(
                            f"{model} unavailable ({e}); trying next Gemini voice model"
                        )
                        break
                    max_tries = ratelimit.attempts_for(e, max_tries)
                    if i < max_tries - 1:
                        log.bind(task="TTS").warning(
                            f"{model} attempt {i + 1} failed ({e}); retrying"
                        )
                        delay = ratelimit.backoff_delay(e, i, base)
                        if ratelimit.is_rate_limited(e):
                            ratelimit.note_limit(delay)
                        time.sleep(delay)
                i += 1
            if spoken:
                break
        if not spoken:
            raise RuntimeError(f"Gemini voice synthesis failed: {last_exc}")

    _write_wav(out_path, pcm_all, rate)
    log.bind(task="TTS").info(
        f"Gemini voice={voice_id} (live_voice={live_voice}) model={used_model} "
        f"({len(text or '')} chars, {len(pieces)} call(s), rate={rate}Hz)"
    )
    try:
        from .. import costs

        costs.record(
            provider="gemini",
            model=used_model,
            kind="live_audio" if "live" in used_model.lower() else "tts",
            in_tokens=max(1, len(text or "") // 4),
            out_tokens=int(len(pcm_all) / (rate * 2) * 25),  # ~25 audio tokens/sec
        )
    except Exception:
        pass
    return "gemini"
