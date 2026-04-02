"""Registry of small LLM models (≤3B parameters) for the local sidecar.

Each entry has a HuggingFace download URL, file size, parameter count,
and a human-readable description. The ``list_models()`` helper augments
each entry with a ``downloaded: bool`` flag based on the models directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Model catalogue — only small models (≤3B params) that work well with
# llama-cpp-python and produce reasonable structured JSON output.
# ---------------------------------------------------------------------------

MODELS: list[dict[str, Any]] = [
    {
        "id": "qwen2.5-0.5b",
        "name": "Qwen 2.5 0.5B Instruct",
        "filename": "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "size_mb": 400,
        "params": "0.5B",
        "context": "32K",
        "quality": "Basic",
        "description": "Tiny and ultra-fast. Good for simple tasks, limited JSON quality.",
    },
    {
        "id": "llama-3.2-1b",
        "name": "Llama 3.2 1B Instruct",
        "filename": "Llama-3.2-1B-Instruct-Q4_K_S.gguf",
        "url": "https://huggingface.co/unsloth/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_S.gguf",
        "size_mb": 700,
        "params": "1B",
        "context": "8K",
        "quality": "Basic",
        "description": "Fast and lightweight. Adequate for basic scoring and extraction.",
    },
    {
        "id": "qwen2.5-1.5b",
        "name": "Qwen 2.5 1.5B Instruct",
        "filename": "Qwen2.5-1.5B-Instruct-Q4_K_M.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "size_mb": 1000,
        "params": "1.5B",
        "context": "32K",
        "quality": "Good",
        "description": "Good balance of speed and quality. Reliable JSON output.",
    },
    {
        "id": "smollm2-1.7b",
        "name": "SmolLM2 1.7B Instruct",
        "filename": "SmolLM2-1.7B-Instruct-Q4_K_M.gguf",
        "url": "https://huggingface.co/bartowski/SmolLM2-1.7B-Instruct-GGUF/resolve/main/SmolLM2-1.7B-Instruct-Q4_K_M.gguf",
        "size_mb": 1100,
        "params": "1.7B",
        "context": "8K",
        "quality": "Good",
        "description": "Compact with strong instruction-following for its size.",
    },
    {
        "id": "llama-3.2-3b",
        "name": "Llama 3.2 3B Instruct",
        "filename": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "url": "https://huggingface.co/unsloth/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "size_mb": 1800,
        "params": "3B",
        "context": "8K",
        "quality": "Good",
        "description": "Default choice. Reliable JSON, good scoring quality.",
    },
    {
        "id": "qwen2.5-3b",
        "name": "Qwen 2.5 3B Instruct",
        "filename": "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        "size_mb": 2000,
        "params": "3B",
        "context": "32K",
        "quality": "Good+",
        "description": "Strong 3B model with long context. Excellent structured output.",
    },
]


def list_models(models_dir: Path) -> list[dict[str, Any]]:
    """Return model registry entries with ``downloaded`` and ``active`` flags."""
    result = []
    for m in MODELS:
        entry = dict(m)
        filepath = models_dir / m["filename"]
        entry["downloaded"] = filepath.exists()
        if entry["downloaded"]:
            try:
                entry["actual_size_mb"] = round(filepath.stat().st_size / (1024 * 1024))
            except OSError:
                entry["actual_size_mb"] = None
        result.append(entry)
    return result


def get_model_by_id(model_id: str) -> dict[str, Any] | None:
    """Look up a model entry by its ID."""
    for m in MODELS:
        if m["id"] == model_id:
            return dict(m)
    return None


def get_models_dir() -> Path:
    """Return the models directory (project root / models)."""
    # Walk up from this file: web/model_registry.py → web → job_hunter → src → project_root
    return Path(__file__).resolve().parent.parent.parent.parent / "models"

