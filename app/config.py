"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = BASE_DIR / "workspace"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Blank KEY= lines in .env must not wipe typed defaults (ints, bools).
        env_ignore_empty=True,
    )

    # ---------------------------------------------------------------- LLM ----
    # Text, vision, speech, and images use Gemini. Older "ollama" / "openai"
    # values in .env are ignored.
    llm_provider: str = "gemini"

    # --- Gemini (cloud) ---
    gemini_api_key: str = ""
    # Slides, VLM, and cron. Only gemini-3.8-flash or gemini-4-argon.
    # https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
    gemini_model: str = "gemini-3.8-flash"
    gemini_vision_model: str = "gemini-3.8-flash"
    # Topic briefs use the same Flash model at thinking level low.
    # 3.8 Flash does not support a separate Flash-Lite id.
    gemini_lite_model: str = "gemini-3.8-flash"
    # Interview Prep vision. Empty = gemini_lite_model.
    interview_vlm_model: str = ""
    # Gemini Live model for mock interviews + C.H.I.T.T.I. voice (full-duplex audio).
    # 3.8 Flash does not support the Live API. Voice uses Gemini 3.8 Live.
    # https://ai.google.dev/gemini-api/docs/models
    interview_live_model: str = "gemini-3.8-live"
    # Empty = same as interview_live_model.
    chitti_live_model: str = ""
    # Gemini Live prebuilt voice for mock interviews + C.H.I.T.T.I.
    # Female: Aoede, Kore, Leda, Zephyr. Male: Puck, Charon, Fenrir, Orus.
    gemini_live_voice: str = "Aoede"
    # Gemini embedding model for sqlite-vec semantic search (Interview Prep).
    gemini_embedding_model: str = "text-embedding-004"
    gemini_embedding_dims: int = 768
    # Nano Banana Pro — thumbnail IMAGE generation ONLY (never used for slides/content).
    # See https://ai.google.dev/gemini-api/docs/image-generation
    gemini_image_model: str = "gemini-3-pro-image"

    # --- Resilience & concurrency ---
    # Transient failures (rate limits, timeouts, empty JSON) are retried with
    # exponential backoff instead of breaking the run.
    llm_max_retries: int = 3
    llm_retry_base_delay: float = 1.5
    llm_timeout_seconds: float = 600.0
    # Max Gemini calls to run at once. 0 = 2, so a slide build is less likely to hit "Rate exceeded".
    llm_max_parallel: int = 3
    # VLM page extraction: small page-groups so one call is not handed a huge
    # multi-image prompt. Groups run in parallel up to llm_max_parallel.
    vlm_pages_per_call: int = 2

    @property
    def active_provider(self) -> str:
        """Text, vision, speech, and images all go through Gemini."""
        return "gemini"

    @property
    def llm_text_model(self) -> str:
        return self.gemini_model

    @property
    def llm_vision_model(self) -> str:
        return self.gemini_vision_model

    def effective_llm_parallel(self) -> int:
        """How many Gemini calls may run at once."""
        n = int(self.llm_max_parallel or 0)
        return n if n > 0 else 2

    # --- Authentication (invite-only) ---
    # Bootstrap admin is controlled by AUTH_ADMIN_* in .env (synced on startup).
    # Registration is invite-only: visitors request an invite, an admin approves
    # it from the dashboard, and only then can that email set a password.
    auth_admin_email: str = "macharlasaiteja@gmail.com"
    auth_admin_username: str = "admin"
    auth_admin_password: str = "saiteja"
    # Secret used to sign session cookies. Change in production via .env.
    auth_secret_key: str = "change-me-multimodal-studio-secret"
    # Session lifetime in hours (only when AUTH_SESSION_PERSISTENT=true).
    auth_session_hours: int = 720
    # When false (default), the session cookie is cleared when the browser/app
    # closes and the user must sign in again on the next visit.
    auth_session_persistent: bool = False
    # Public demo mode for the hosted judging link (Cloud Run).
    # DEMO_PUBLIC=true skips the login screen entirely: every visitor gets a
    # shared "demo" identity and lands straight in Studio. Local runs keep
    # invite-only login (default false). Never commit secrets alongside this.
    demo_public: bool = False

    @field_validator(
        "auth_admin_email",
        "auth_admin_username",
        "auth_admin_password",
        "auth_secret_key",
        mode="before",
    )
    @classmethod
    def _blank_auth_keeps_default(cls, v: Any, info: ValidationInfo) -> Any:
        """Empty AUTH_* in .env must not wipe defaults (Pydantic treats '' as set)."""
        defaults = {
            "auth_admin_email": "macharlasaiteja@gmail.com",
            "auth_admin_username": "admin",
            "auth_admin_password": "saiteja",
            "auth_secret_key": "change-me-multimodal-studio-secret",
        }
        if v is None or (isinstance(v, str) and not v.strip()):
            return defaults[info.field_name]
        return v.strip() if isinstance(v, str) else v

    # --- Cost tracking (admin only) ---
    # Estimated $ cost of LLM/paid-API usage is derived from per-model rates.
    # Provide overrides as a comma-separated list of
    #   model=INPUT_RATE/OUTPUT_RATE   (USD per 1M tokens),
    # e.g. "gemini-2.5-flash=0.30/2.50,gpt-4o-mini=0.15/0.60". Unknown models
    # fall back to built-in defaults, then to $0 (treated as free/local).
    cost_rates: str = ""

    # --- Voice library limits (saved cloned voices per account) ---
    voice_limit_user: int = 10
    voice_limit_admin: int = 30

    def cost_rate_map(self) -> dict[str, tuple[float, float]]:
        """Parse ``cost_rates`` into {model: (in_rate, out_rate)}."""
        out: dict[str, tuple[float, float]] = {}
        for part in (self.cost_rates or "").split(","):
            part = part.strip()
            if not part or "=" not in part:
                continue
            model, _, rates = part.partition("=")
            try:
                in_s, _, out_s = rates.partition("/")
                out[model.strip().lower()] = (float(in_s), float(out_s or in_s))
            except (TypeError, ValueError):
                continue
        return out

    youtube_client_secrets: str = "client_secrets.json"
    youtube_privacy: str = "unlisted"
    # Public https origin of this app (no trailing slash). Required for Instagram
    # Graph API to fetch staged carousel/video URLs under /files/{job}/….
    public_base_url: str = ""

    # --- Publish metadata + thumbnail generation ---
    # Auto-generate a YouTube title/description/tags (via the active LLM) and a
    # thumbnail image before upload. Both are best-effort: if they fail the flow
    # continues with a sensible fallback and a message is surfaced.
    generate_thumbnail: bool = True
    # Optional Pillow title overlay (unused for Nano Banana which renders text).
    thumbnail_text_overlay: bool = True
    # Thumbnail image backend:
    #   "nano_banana" / "gemini" / "auto" -> Gemini Nano Banana Pro (cloud).
    #   Thumbnail-only — never used for slide/content gen.
    #   "off" / "none" -> skip AI thumbnail (slide-frame fallback may still apply).
    # Local diffusion (Boogu etc.) removed for now — re-add later as optional.
    thumbnail_backend: str = "auto"
    # Experimental: AI images inside slides (UI default off until validated).
    slide_ai_images_default: bool = False

    # PDF page -> image rendering resolution for VLM extraction.
    vlm_dpi: int = 150
    vlm_max_pages: int = 15

    capture_duration: int = 20
    capture_fps: int = 24

    # Output video quality tier: "1080p" (1920x1080), "2k"/"1440p" (2560x1440),
    # or "4k" (3840x2160). Vertical formats scale to the matching short edge.
    # Slides are laid out at a logical 1080-line canvas and captured at a higher
    # device pixel ratio so text/vectors stay razor-sharp when scaled up.
    video_quality: str = "2k"
    # x264 constant-rate-factor (lower = better quality/larger file; 18-23 sane).
    video_crf: int = 20
    # Encode speed. "slow" looks marginally better but makes long (8‑min) merges
    # crawl across dozens of slide segments — use medium as the studio default.
    video_preset: str = "medium"

    # Animation mode for slide capture:
    #   "animated" -> deterministic frame-by-frame seek capture (real in-slide
    #                 motion driven by the page's frame timeline; slower render).
    #   "still"    -> one screenshot per slide (fastest; motion only via
    #                 crossfades between slides).
    animation_mode: str = "animated"
    # Seconds of entrance motion captured frame-by-frame per slide. After this
    # window the slide is static, so we capture ONE settled frame and let ffmpeg
    # hold it for the rest of the narration — this keeps real motion while
    # cutting the screenshot count from thousands to a few dozen per slide.
    motion_seconds: float = 2.0
    # Frames per second used for the seek-captured motion window. Lower than the
    # final video fps because entrance + highlight transitions read fine at a
    # modest rate, and every extra fps multiplies the screenshot count. The final
    # video is still encoded at capture_fps; ffmpeg upsamples the held frames.
    motion_fps: int = 24
    # When True the item highlight (active bullet/step/stat) walks through the
    # slide IN TIME with the narration: the whole slide duration is seek-captured
    # at motion_fps so on-screen focus stays synced to what's being explained.
    # When False only the short entrance window is captured (faster, no walk).
    sync_highlight: bool = True
    # Max PNG screenshots per slide during animated capture. When the narration-
    # synced timeline would exceed this, only entrance + highlight-transition
    # keyframes are captured (timing is preserved via keyframes.json).
    max_motion_frames: int = 144
    # How many Playwright worker processes capture slides in parallel. Slides are
    # split into this many disjoint batches, each rendered in its own browser,
    # then the segments are assembled. 0 = auto (based on CPU count).
    capture_workers: int = 0

    # Gemini voice narration (expressive human voice). gemini-3.8-live (primary) or gemini-3.8-flash-tts.
    gemini_tts_model: str = "gemini-3.8-live"
    gemini_tts_voice: str = "Kore"

    @property
    def youtube_secrets_path(self) -> Path:
        p = Path(self.youtube_client_secrets)
        return p if p.is_absolute() else BASE_DIR / p

    # ===================================================== paper automation ====
    # Built-in "paper -> video" automation. Runs in-process (reuses the same
    # pipeline the UI uses) so it never needs to call the app over HTTP. The
    # admin controls it from the dashboard; it can also run on a schedule.
    # Master switch for the background scheduler (manual trigger always works).
    auto_enabled: bool = False
    # Paper source:
    #   "trending" / "auto" -> HF Daily Papers + arXiv GenAI + Semantic Scholar
    #   "hf"                -> Hugging Face Daily Papers only
    #   "arxiv"             -> arXiv Atom API
    #   "semantic_scholar"  -> Semantic Scholar bulk search
    auto_source: str = "trending"
    auto_arxiv_categories: str = "cs.LG,cs.CL,cs.CV,cs.AI,cs.NE,stat.ML"
    # Extra arXiv abs/ti keyword clause (GenAI-focused). Empty = built-in defaults.
    auto_arxiv_keywords: str = ""
    auto_ss_query: str = (
        "large language model OR multimodal OR diffusion OR generative AI "
        "OR vision language OR agentic OR RLHF"
    )
    auto_ss_api_key: str = ""
    # Only keep papers that reference a code repo (keeps the "with code" spirit).
    # When none match, the engine falls back to newest PDF with a log note.
    auto_require_code: bool = True
    # How many papers to process per run (preview UI shows the same queue, up to 3).
    auto_max_papers: int = 3
    # Outputs per paper.
    auto_make_video: bool = True
    auto_video_format: str = "youtube_video"
    auto_video_seconds: int = 480  # 8 min default for cron papers
    # Reels/Instagram are a manual Studio format choice — cron/automation renders
    # long-form video only so one paper does not spawn two History jobs.
    auto_make_reel: bool = False
    auto_reel_format: str = "instagram_reels"
    auto_reel_seconds: int = 45
    auto_video_theme: str = "snow"
    auto_video_style: str = "whiteboard"  # Teach · board
    # Gemini studio voice for cron narration (Kore, Charon, Aoede, …).
    auto_gemini_voice: str = "Kore"
    # Generate a thumbnail into the paper output folder after each render.
    auto_generate_thumbnail: bool = True
    # Push to YouTube after render (only if client_secrets.json is present).
    auto_publish: bool = False
    # Root folder for per-paper output folders.
    auto_output_dir: str = "automation_output"
    # --- schedule: run twice a day in non-working hours ---
    # Comma-separated hours-of-day (0-23). Default 01:00 & 19:00 stay outside
    # the 9–18 working-hours block (13:00 was previously blocked by that window).
    auto_run_hours: str = "1,19"
    # Working-hours window to AVOID auto-runs in (inclusive start, exclusive end).
    auto_work_start_hour: int = 9
    auto_work_end_hour: int = 18
    # --- compute gating (only run when the machine is free) ---
    auto_compute_gate: bool = True
    auto_max_load: float = 0.85
    auto_min_gpu_free_mb: int = 0
    auto_compute_wait_seconds: int = 1800

    # --- Firebase & Cloud Firestore ---
    firestore_enabled: bool = True
    firestore_collection: str = "studio_jobs"

    # --- Topic web research ("Add topics" in More options) ---
    # When the user lists extra topics, we scrape/search the public web and
    # inject fresh facts/examples into slide generation. Free backends
    # (DuckDuckGo via ``ddgs``, Wikipedia, Jina Reader) always run; optional
    # API keys below are raced first for speed/quality when present.
    topic_research_enabled: bool = True
    topic_research_max_results: int = 4
    topic_research_fetch_pages: int = 2
    topic_research_timeout: float = 8.0
    brave_api_key: str = ""
    tavily_api_key: str = ""
    serper_api_key: str = ""

    @property
    def auto_output_path(self) -> Path:
        p = Path(self.auto_output_dir)
        return p if p.is_absolute() else BASE_DIR / p


@lru_cache
def get_settings() -> Settings:
    return Settings()
