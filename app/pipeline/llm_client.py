"""Provider-agnostic LLM client (text + vision).

Text, vision, and images go through the Gemini API (``google-genai``).

The rest of the pipeline only calls :func:`generate_text` and
:func:`generate_from_images`; switching vendors is purely a config change.
"""
from __future__ import annotations

import base64
import random
import time
from pathlib import Path

from ..config import get_settings
from ..logging_setup import log


class LLMError(RuntimeError):
    """Raised when an LLM/VLM call fails after all retries."""


# Text and vision stay on the two current models.
# https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
# https://ai.google.dev/gemini-api/docs/models (Gemini 4 Argon)
_GEMINI_TEXT_MODELS = frozenset({"gemini-3.8-flash", "gemini-4-argon"})


def normalize_gemini_model(model: str | None) -> str:
    """Map any older Gemini text/vision id to 3.8 Flash or keep 4 Argon.

    3.7 Flash is deprecated and Google routes it to ``gemini-3.8-flash``.
    TTS, Live, image, and embedding ids are not passed through here.
    """
    key = (model or "").strip()
    if not key:
        return "gemini-3.8-flash"
    low = key.lower()
    if low in _GEMINI_TEXT_MODELS:
        return low
    if low.startswith("gemini"):
        return "gemini-3.8-flash"
    return key


def _thinking_level(*, structured: bool) -> str:
    """3.8 Flash and 4 Argon accept low, medium, or high. ``minimal`` errors."""
    return "low" if structured else "medium"


def _output_token_cap(requested: int | None) -> int | None:
    """Argon and 3.8 Flash spend part of the cap on thinking.

    Google recommends at least 32,768 when a cap is set. Sync calls error
    above 65,536.
    """
    if not requested:
        return None
    cap = int(requested)
    if cap < 32768:
        cap = 32768
    if cap > 65536:
        cap = 65536
    return cap


def _response_text(response) -> str:
    """Answer text only. Thought parts are not the slide JSON."""
    text = getattr(response, "text", None) or ""
    if text.strip():
        return text
    candidates = getattr(response, "candidates", None) or []
    parts = []
    if candidates and getattr(candidates[0], "content", None) is not None:
        parts = getattr(candidates[0].content, "parts", None) or []
    chunks: list[str] = []
    for part in parts:
        if getattr(part, "thought", False):
            continue
        bit = getattr(part, "text", None)
        if bit:
            chunks.append(bit)
    return "".join(chunks)


def _retryable(fn, *, task: str, provider: str, model: str):
    """Run ``fn`` with exponential backoff on transient failures.

    A single attempt that returns an EMPTY string is treated as a soft failure
    and retried too (local models occasionally burn tokens on reasoning and
    return nothing). Never swallows the final error — the caller decides how to
    degrade (e.g. fall back to a template).
    """
    settings = get_settings()
    attempts = max(1, int(settings.llm_max_retries))
    base = float(settings.llm_retry_base_delay)
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            out = fn()
            if out and out.strip():
                return out
            # Empty output: retry (unless it was the last attempt).
            last_exc = LLMError("model returned empty content")
            if i < attempts - 1:
                log.bind(task=task).warning(
                    f"empty response ({provider}/{model}), retry {i + 1}/{attempts - 1}"
                )
        except Exception as e:  # noqa: BLE001 - classify + backoff below
            last_exc = e
            if i < attempts - 1:
                log.bind(task=task).warning(
                    f"{provider}/{model} attempt {i + 1} failed ({e}); retrying"
                )
        if i < attempts - 1:
            # Exponential backoff with jitter to avoid hammering a busy backend.
            time.sleep(base * (2 ** i) + random.uniform(0, base * 0.5))
    # Exhausted retries.
    if isinstance(last_exc, Exception):
        raise last_exc
    raise LLMError(f"{provider}/{model} produced no content")


def _mime_for(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".gif": "image/gif",
    }.get(ext, "image/png")


# ----------------------------------------------------------------- Gemini ----

def _gemini_client():
    from google import genai

    settings = get_settings()
    return genai.Client(api_key=settings.gemini_api_key)


def _gemini_generate_text(
    prompt: str,
    want_json: bool = False,
    *,
    model: str | None = None,
    use_search: bool = False,
    max_output_tokens: int | None = None,
) -> str:
    settings = get_settings()
    client = _gemini_client()
    from google.genai import types

    tools = []
    if use_search and not want_json:
        tools.append(types.Tool(google_search=types.GoogleSearch()))

    config_kwargs: dict = {}
    if want_json:
        config_kwargs["response_mime_type"] = "application/json"
    if tools:
        config_kwargs["tools"] = tools
    cap = _output_token_cap(max_output_tokens)
    if cap:
        config_kwargs["max_output_tokens"] = cap
    try:
        config_kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_level=_thinking_level(structured=want_json)
        )
    except Exception:
        config_kwargs.pop("thinking_config", None)
    config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

    response = client.models.generate_content(
        model=normalize_gemini_model(model or settings.gemini_model),
        contents=prompt,
        **({"config": config} if config else {}),
    )
    text = _response_text(response)
    if not text:
        # A response with no text but a finish_reason of MAX_TOKENS means the
        # model got cut off before emitting anything parseable (common with
        # very large structured JSON asks) — surface a clear, retryable error
        # instead of returning "" and letting the caller guess why.
        finish = None
        try:
            finish = response.candidates[0].finish_reason if response.candidates else None
        except Exception:  # noqa: BLE001
            finish = None
        if finish and "MAX_TOKENS" in str(finish).upper():
            raise LLMError(
                f"Gemini response truncated at max_output_tokens ({finish})"
            )
    return text


def _gemini_generate_from_images(
    prompt: str, image_paths: list[Path], *, model: str | None = None
) -> str:
    from google.genai import types

    settings = get_settings()
    client = _gemini_client()
    model_id = normalize_gemini_model(model or settings.gemini_vision_model)

    parts: list = [prompt]
    for p in image_paths:
        p = Path(p)
        parts.append(
            types.Part.from_bytes(data=p.read_bytes(), mime_type=_mime_for(p))
        )

    config_kwargs: dict = {}
    try:
        config_kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_level=_thinking_level(structured=True)
        )
    except Exception:
        config_kwargs = {}
    config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
    response = client.models.generate_content(
        model=model_id,
        contents=parts,
        **({"config": config} if config else {}),
    )
    return _response_text(response)


def _gemini_generate_image(prompt: str, out_path: Path) -> Path:
    """Nano Banana Pro image generation (thumbnail pipeline only).

    Uses ``gemini_image_model`` (default ``gemini-3-pro-image``). This path is
    intentionally separate from text/vision models so slide content never hits
    the image model.
    """
    from google.genai import types

    settings = get_settings()
    if not (settings.gemini_api_key or "").strip():
        raise LLMError("GEMINI_API_KEY required for Nano Banana Pro thumbnails")

    client = _gemini_client()
    model = (settings.gemini_image_model or "gemini-3-pro-image").strip()
    config = types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"])
    response = client.models.generate_content(
        model=model,
        contents=[prompt],
        config=config,
    )

    parts = getattr(response, "parts", None) or []
    if not parts and getattr(response, "candidates", None):
        content = (response.candidates[0].content if response.candidates else None)
        parts = getattr(content, "parts", None) or []

    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline is None:
            continue
        data = getattr(inline, "data", None)
        if not data:
            continue
        if isinstance(data, str):
            data = base64.b64decode(data)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Prefer PIL save when the SDK exposes as_image(); else write bytes.
        as_image = getattr(part, "as_image", None)
        if callable(as_image):
            try:
                as_image().save(out_path)
                return out_path
            except Exception:  # noqa: BLE001
                pass
        out_path.write_bytes(data)
        return out_path

    raise LLMError(f"Nano Banana Pro ({model}) returned no image data")


# ----------------------------------------------------------------- public ----

def generate_text(
    prompt: str,
    want_json: bool = False,
    *,
    model: str | None = None,
    use_search: bool = False,
    max_output_tokens: int | None = None,
) -> str:
    """Generate text. Set ``want_json=True`` when the reply must be a JSON
    object — this enables provider-native JSON/structured output (Ollama
    ``format=json``, Gemini ``response_mime_type``, OpenAI ``response_format``)
    which prevents models from emitting reasoning/preamble that yields empty or
    unparseable content.

    Optional ``model`` overrides the active text model (``gemini-3.8-flash`` or
    ``gemini-4-argon``). Set ``use_search=True`` to enable Gemini Google Search Grounding.
    ``max_output_tokens`` raises the provider's output cap for large structured
    JSON asks (e.g. an 18-question prep pack) that would otherwise get cut off
    mid-object and fail to parse.
    """
    settings = get_settings()
    provider = "gemini"
    model_id = normalize_gemini_model(model or settings.llm_text_model)
    log.bind(task="LLM").info(f"text generation -> {provider} / {model_id} (search={use_search})")

    def _call() -> str:
        return _gemini_generate_text(
            prompt,
            want_json=want_json,
            model=model_id,
            use_search=use_search,
            max_output_tokens=max_output_tokens,
        )

    try:
        out = _retryable(_call, task="LLM", provider=provider, model=model_id)
        log.bind(task="LLM").success(
            f"text ok ({provider}/{model_id}, {len(out):,} chars)"
        )
        _record_cost(provider, model_id, "text", prompt, out)
        return out
    except Exception as e:
        log.bind(task="LLM").error(f"text failed ({provider}/{model_id}): {e}")
        raise


def generate_from_images(
    prompt: str,
    image_paths: list[Path],
    *,
    model: str | None = None,
) -> str:
    """Multimodal call: send page images + a prompt to the vision model.

    Optional ``model`` overrides the active vision model (``gemini-3.8-flash`` or
    ``gemini-4-argon``).
    """
    settings = get_settings()
    provider = "gemini"
    model_id = normalize_gemini_model(model or settings.llm_vision_model)
    log.bind(task="VLM").info(
        f"vision extraction -> {provider} / {model_id} ({len(image_paths)} image(s))"
    )

    def _call() -> str:
        return _gemini_generate_from_images(prompt, image_paths, model=model_id)

    try:
        out = _retryable(_call, task="VLM", provider=provider, model=model_id)
        log.bind(task="VLM").success(
            f"vision ok ({provider}/{model_id}, {len(out):,} chars)"
        )
        # Image inputs add tokens beyond the text prompt; approximate a flat
        # per-image surcharge so vision costs aren't undercounted.
        _record_cost(provider, model_id, "vision", prompt, out, extra_in=258 * len(image_paths))
        return out
    except Exception as e:
        log.bind(task="VLM").error(f"vision failed ({provider}/{model_id}): {e}")
        raise


def embed_texts(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    """Embed strings with Gemini (or empty list if unavailable).

    Used by Interview Prep sqlite-vec semantic search.
    """
    settings = get_settings()
    if not texts:
        return []
    if not (settings.gemini_api_key or "").strip():
        raise LLMError("GEMINI_API_KEY required for embeddings")
    model_id = (model or settings.gemini_embedding_model or "text-embedding-004").strip()
    client = _gemini_client()
    out: list[list[float]] = []
    # Batch small groups to avoid oversized requests.
    batch = 16
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        for t in chunk:
            resp = client.models.embed_content(model=model_id, contents=t or " ")
            emb = None
            if hasattr(resp, "embeddings") and resp.embeddings:
                emb = list(resp.embeddings[0].values)
            elif hasattr(resp, "embedding") and resp.embedding is not None:
                emb = list(getattr(resp.embedding, "values", resp.embedding) or [])
            if not emb:
                raise LLMError(f"embedding empty for model {model_id}")
            out.append(emb)
    return out


def generate_image(prompt: str, out_path: Path) -> Path:
    """Generate an image with Gemini Nano Banana Pro (thumbnails only).

    Never used for slide/content generation — those stay on ``gemini_model`` /
    ``gemini_vision_model``. Requires ``GEMINI_API_KEY``.
    """
    settings = get_settings()
    model = (settings.gemini_image_model or "gemini-3-pro-image").strip()
    log.bind(task="IMG").info(f"Nano Banana Pro thumbnail -> gemini / {model}")

    attempts = max(1, int(settings.llm_max_retries))
    base = float(settings.llm_retry_base_delay)
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            path = _gemini_generate_image(prompt, Path(out_path))
            if path and Path(path).is_file() and Path(path).stat().st_size > 0:
                log.bind(task="IMG").success(
                    f"image ok (gemini/{model} -> {Path(path).name})"
                )
                _record_cost("gemini", model, "image", prompt, "", extra_in=0)
                return Path(path)
            last_exc = LLMError("Nano Banana Pro returned an empty image file")
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if i < attempts - 1:
                log.bind(task="IMG").warning(
                    f"gemini/{model} attempt {i + 1} failed ({e}); retrying"
                )
        if i < attempts - 1:
            time.sleep(base * (2 ** i) + random.uniform(0, base * 0.5))

    log.bind(task="IMG").error(f"image failed (gemini/{model}): {last_exc}")
    if isinstance(last_exc, Exception):
        raise last_exc
    raise LLMError(f"gemini/{model} produced no image")


def _record_cost(
    provider: str, model: str, kind: str, prompt: str, output: str, extra_in: int = 0
) -> None:
    """Record an estimated-cost usage event (best-effort, never raises)."""
    try:
        from .. import costs

        it = costs.estimate_tokens(prompt) + int(extra_in)
        costs.record(
            provider=provider, model=model, kind=kind,
            in_tokens=it, out_tokens=costs.estimate_tokens(output),
        )
    except Exception:  # noqa: BLE001
        pass


def parallel_map(fn, items: list, ordered: bool = True) -> list:
    """Run ``fn`` over ``items`` with the provider-appropriate concurrency.

    * Cloud providers fan out up to ``llm_max_parallel`` calls at once.
    * A local Ollama instance is clamped to 1 (sequential) since concurrent
      requests to one model just queue and risk OOM.

    Exceptions from individual items are NOT swallowed here — the returned list
    preserves input order and each slot is either the result or the raised
    Exception instance, so callers can decide how to degrade per-item.
    """
    if not items:
        return []
    workers = get_settings().effective_llm_parallel()
    results: list = [None] * len(items)
    if workers <= 1 or len(items) == 1:
        for i, it in enumerate(items):
            try:
                results[i] = fn(it)
            except Exception as e:  # noqa: BLE001 - captured per-item
                results[i] = e
        return results
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, it): i for i, it in enumerate(items)}
        for fut in futs:
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as e:  # noqa: BLE001
                results[i] = e
    return results
