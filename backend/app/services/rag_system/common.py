"""
Shared helpers used by both build_index.py and retrieve_and_estimate.py.
"""
import uuid
import math
import pandas as pd


def _clean(v, default=None):
    """Turn NaN/None into a clean default; pass everything else through."""
    if v is None:
        return default
    if isinstance(v, float) and math.isnan(v):
        return default
    if isinstance(v, str) and v.strip() == "":
        return default
    return v


def parse_skills(skills_raw):
    """skills column is '|' separated -> list[str]"""
    skills_raw = _clean(skills_raw, "")
    if not skills_raw:
        return []
    return [s.strip() for s in str(skills_raw).split("|") if s.strip()]


def to_point_id(project_id):
    """
    Qdrant point IDs must be an unsigned int or a UUID.
    If project_id is already integer-like, use it directly (fast, human-traceable).
    Otherwise derive a stable UUID from it so re-running ingestion is idempotent.
    """
    try:
        return int(project_id)
    except (TypeError, ValueError):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, str(project_id)))


def build_document_text(row: dict) -> str:
    """
    Build the natural-language text that gets embedded.
    Only semantically meaningful fields go in here — numeric price/budget
    fields are deliberately left OUT of the text and stored as payload
    metadata instead, since raw numbers embed poorly and would pollute
    the semantic similarity signal.
    """
    parts = []

    title = _clean(row.get("title"))
    if title:
        parts.append(f"عنوان: {title}")

    category = _clean(row.get("category"))
    if category:
        parts.append(f"دسته‌بندی: {category}")

    organization = _clean(row.get("organization"))
    if organization:
        parts.append(f"کارفرما: {organization}")

    skills = parse_skills(row.get("skills"))
    if skills:
        parts.append(f"مهارت‌های موردنیاز: {', '.join(skills)}")

    duration = _clean(row.get("duration"))
    if duration:
        parts.append(f"مدت زمان پروژه: {duration}")

    description = _clean(row.get("description"))
    if description:
        parts.append(f"توضیحات: {description}")

    return "\n".join(parts)


def build_payload(row: dict, document_text: str) -> dict:
    """
    Everything the retriever / downstream LLM prompt will need,
    without re-reading the CSV. Keep this rich but flat.
    """
    skills = parse_skills(row.get("skills"))

    def num(col):
        v = _clean(row.get(col))
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def s(col):
        return _clean(row.get(col))

    return {
        "project_id": s("project_id"),
        "scraped_project_id": s("scraped_project_id"),
        "title": s("title"),
        "category": s("category"),
        "organization": s("organization"),
        "outer_link": s("outer_link"),
        "scraped_date_created": s("scraped_date_created"),
        "skills": skills,
        "skill_count": int(num("skill_count")) if num("skill_count") is not None else len(skills),
        "duration": s("duration"),
        "duration_days": num("duration_days"),
        "has_final_budget": bool(num("has_final_budget")) if num("has_final_budget") is not None else None,
        "final_budget": num("final_budget"),
        "final_budget_usd": num("final_budget_usd"),
        "budget_min": num("budget_min"),
        "budget_max": num("budget_max"),
        "budget_min_usd": num("budget_min_usd"),
        "budget_max_usd": num("budget_max_usd"),
        "budget_range_usd": num("budget_range_usd"),
        "budget_range_ratio": num("budget_range_ratio"),
        "description_length": num("description_length"),
        "description_word_count": num("description_word_count"),
        # Kept so the retrieved chunk can be dropped straight into an LLM prompt
        # without needing a second lookup.
        "description": document_text,
    }


def embed_text(model, text: str, is_query: bool):
    """Apply the e5 asymmetric prefix convention if configured."""
    from app.core.config import settings
    if settings.USE_E5_PREFIXES:
        prefix = "query: " if is_query else "passage: "
        return prefix + text
    return text
