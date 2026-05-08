"""Embedding helper functions for semantic similarity."""

# SWAP_POINT: This entire module will be replaced with Anthropic
# embeddings API calls from Phase 5 onward.
# Search for SWAP_POINT across the codebase when Phase 5 begins.

from __future__ import annotations

from math import sqrt

from sentence_transformers import SentenceTransformer

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    """Lazy load embeddings model once for process lifetime."""
    global _model
    if _model is None:
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def compute_similarity(text_a: str, text_b: str) -> float:
    """Return cosine similarity between two texts on [0.0, 1.0]."""
    clean_a = text_a.strip()
    clean_b = text_b.strip()
    if not clean_a and not clean_b:
        return 1.0
    if not clean_a or not clean_b:
        return 0.0

    # SWAP_POINT: replace internals with Anthropic embeddings API
    try:
        model = get_model()
        vec_a, vec_b = model.encode([clean_a, clean_b])
        dot = float((vec_a * vec_b).sum())
        norm_a = sqrt(float((vec_a * vec_a).sum()))
        norm_b = sqrt(float((vec_b * vec_b).sum()))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        similarity = dot / (norm_a * norm_b)
        return max(0.0, min(1.0, similarity))
    except Exception:
        # Lightweight fallback keeps tests deterministic in offline environments.
        tokens_a = set(clean_a.lower().split())
        tokens_b = set(clean_b.lower().split())
        if not tokens_a and not tokens_b:
            return 1.0
        if not tokens_a or not tokens_b:
            return 0.0
        overlap = len(tokens_a & tokens_b)
        denominator = max(len(tokens_a), len(tokens_b))
        return overlap / denominator
