"""Entity normalisation — canonical names, alias resolution, fuzzy matching."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from job_hunter.market.db_models import EntityType, MarketAlias, MarketEntity

logger = logging.getLogger("job_hunter.market.normalize")

_DATA_DIR = Path(__file__).parent / "data"
_ALIASES_PATH = _DATA_DIR / "aliases.yml"

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def canonicalize(text: str) -> str:
    """Lower-case, strip, collapse whitespace, remove trailing punctuation."""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip(".,;:")
    return text


# ---------------------------------------------------------------------------
# Alias dictionary
# ---------------------------------------------------------------------------

_alias_cache: dict[str, str] | None = None


def load_aliases(path: Path | None = None) -> dict[str, str]:
    """Return a mapping ``alias → canonical_name`` from the YAML file.

    The result is cached after the first call.
    """
    global _alias_cache
    if _alias_cache is not None:
        return _alias_cache

    path = path or _ALIASES_PATH
    mapping: dict[str, str] = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            raw: dict[str, list[str]] = yaml.safe_load(f) or {}
        for canonical, aliases in raw.items():
            canonical_lower = canonicalize(canonical)
            # The canonical name is also an alias for itself
            mapping[canonical_lower] = canonical_lower
            for alias in aliases:
                mapping[canonicalize(alias)] = canonical_lower
    _alias_cache = mapping
    return mapping


def reset_alias_cache() -> None:
    """Clear the in-memory alias cache (useful for tests)."""
    global _alias_cache
    _alias_cache = None


# ---------------------------------------------------------------------------
# Entity resolution
# ---------------------------------------------------------------------------


def resolve_alias(text: str, aliases: dict[str, str] | None = None) -> str:
    """Map *text* to its canonical form via the alias dictionary."""
    aliases = aliases or load_aliases()
    key = canonicalize(text)
    return aliases.get(key, key)


def resolve_or_create_entity(
    session: Session,
    entity_type: EntityType,
    raw_name: str,
    *,
    aliases: dict[str, str] | None = None,
    fuzzy_threshold: int = 88,
) -> MarketEntity:
    """Find or create a :class:`MarketEntity`.

    Resolution order:

    1. Exact match on ``(entity_type, canonical_name)``
    2. Alias lookup in the DB ``market_aliases`` table
    3. Alias lookup in the YAML dictionary
    4. Fuzzy match against existing entities of the same type
    5. Create a new entity
    """
    aliases = aliases or load_aliases()
    canonical = resolve_alias(raw_name, aliases)

    # 1. Exact entity match
    entity = session.execute(
        select(MarketEntity).where(
            MarketEntity.entity_type == entity_type,
            MarketEntity.canonical_name == canonical,
        )
    ).scalar_one_or_none()
    if entity is not None:
        return entity

    # 2. DB alias lookup
    db_alias = session.execute(
        select(MarketAlias).where(MarketAlias.alias_text == canonical)
    ).scalar_one_or_none()
    if db_alias is not None:
        entity = session.get(MarketEntity, db_alias.entity_id)
        if entity is not None:
            return entity

    # 3. Fuzzy match against existing entities of the same type
    existing = session.execute(
        select(MarketEntity).where(MarketEntity.entity_type == entity_type)
    ).scalars().all()
    best_score = 0
    best_entity: MarketEntity | None = None
    for ent in existing:
        score = fuzz.ratio(canonical, ent.canonical_name)
        if score > best_score:
            best_score = score
            best_entity = ent
    if best_entity is not None and best_score >= fuzzy_threshold:
        # Persist the alias so future lookups are instant
        _persist_alias(session, canonical, best_entity.id)
        return best_entity

    # 4. Create new entity
    display = raw_name.strip() or canonical
    entity = MarketEntity(
        entity_type=entity_type,
        canonical_name=canonical,
        display_name=display,
    )
    session.add(entity)
    session.flush()
    logger.debug("Created entity %s:%s (id=%d)", entity_type.value, canonical, entity.id)
    return entity


def _persist_alias(session: Session, alias_text: str, entity_id: int) -> None:
    """Insert a DB alias row, ignoring duplicates."""
    existing = session.execute(
        select(MarketAlias).where(MarketAlias.alias_text == alias_text)
    ).scalar_one_or_none()
    if existing is None:
        session.add(MarketAlias(alias_text=alias_text, entity_id=entity_id))
        session.flush()

