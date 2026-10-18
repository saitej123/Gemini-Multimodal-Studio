"""Backwards-compatible shim.

The LLM layer lives in :mod:`llm_client` (Gemini only). This module is kept so existing
``from . import gemini_client`` imports continue to work.
"""
from __future__ import annotations

from .llm_client import generate_from_images, generate_text, parallel_map

__all__ = ["generate_text", "generate_from_images", "parallel_map"]
