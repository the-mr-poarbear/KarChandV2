# app/services/project_matcher.py
"""
Scores every project in full_dataset.csv against a taxonomy (features,
modifiers, application types, etc.) and returns the closest matches.
"""
from pathlib import Path
from threading import Lock
from typing import Optional

from sqlalchemy.orm import Session

from app.services.usd import get_current_usd_rate

import pandas as pd

DATASET_PATH = Path(__file__).parent.parent.parent.parent / "ml_dataset" / "full_dataset.csv"

import json

TAXONOMY_PATH = Path(__file__).parent.parent.parent / "taxonomy/taxonomy.json"

with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
    TAXONOMY_CONFIG = json.load(f)

_df: Optional[pd.DataFrame] = None
_df_lock = Lock()


# ---------------------------------------------------------------------------
# Dataset loading (cached — the CSV is only read from disk once per process)
# ---------------------------------------------------------------------------
def _dedupe_columns(columns: list[str]) -> list[str]:
    """
    full_dataset.csv has 'outer_link' twice in the header. pandas would
    otherwise silently overwrite the first occurrence with the second —
    rename the repeat instead of losing a column.
    """
    seen: dict[str, int] = {}
    out = []
    for col in columns:
        if col not in seen:
            seen[col] = 0
            out.append(col)
        else:
            seen[col] += 1
            out.append(f"{col}.{seen[col]}")
    return out


def _load_dataset() -> pd.DataFrame:
    global _df
    if _df is not None:
        return _df
    with _df_lock:
        if _df is not None:
            return _df
        df = pd.read_csv(DATASET_PATH, dtype=str, keep_default_na=False)
        df.columns = _dedupe_columns(list(df.columns))
        _df = df
        return _df


def reload_dataset() -> pd.DataFrame:
    """Call this if full_dataset.csv changes on disk and you want the
    in-memory cache refreshed without restarting the process."""
    global _df
    with _df_lock:
        _df = None
    return _load_dataset()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _num(val, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default

FEATURE_WEIGHT = 3.0
BOOLEAN_WEIGHT = 2.0
APP_TYPE_WEIGHT = 4.0
TECH_WEIGHT = 3.0
ENUM_WEIGHT = 3.0

FEATURE_THRESHOLD = 1.0

# Small penalty for candidate features that the user did not request.
EXTRA_FEATURE_PENALTY = 0.30


def _score_row(row: pd.Series, taxonomy: dict) -> float:
    """
    Calculate normalized taxonomy similarity between a candidate
    project and the user's project taxonomy.

    Feature scores in the dataset are 0-5, but are treated as
    binary during matching:

        0, 1 -> absent
        2, 3, 4, 5 -> present

    All scoring categories are normalized to [0, 1] before
    applying their respective weights.
    """

    # ============================================================
    # 1. FEATURES
    # ============================================================

    requested_features = set(taxonomy.get("features", []))

    # Complete list from taxonomy.json
    all_features = set(
        TAXONOMY_CONFIG
        .get("features", {})
        .get("values", [])
    )

    matched_features = 0

    # ------------------------------------------------------------
    # Requested features
    # ------------------------------------------------------------

    for feat in requested_features:

        if feat not in row.index:
            continue

        project_value = _num(row[feat])

        # 0 and 1 = absent
        # 2+ = present
        if project_value >= FEATURE_THRESHOLD:
            matched_features += 1

    # Normalize requested feature matches to [0, 1]
    if requested_features:
        feature_similarity = (
            matched_features / len(requested_features)
        )
    else:
        feature_similarity = 0.0

    # ------------------------------------------------------------
    # Candidate-only features
    # ------------------------------------------------------------

    extra_features = 0

    for feat in all_features:

        # Don't penalize a feature that the user requested.
        if feat in requested_features:
            continue

        if feat not in row.index:
            continue

        project_value = _num(row[feat])

        # Only >= 2 counts as an actual feature.
        if project_value >= FEATURE_THRESHOLD:
            extra_features += 1

    # Small normalized penalty.
    #
    # Example:
    # 5 extra features / 90 total features
    # = 0.0556
    #
    # penalty = 0.0556 * 0.10
    #         = 0.0056
    #
    # This keeps the penalty intentionally small.
    if all_features:
        extra_feature_ratio = (
            extra_features / len(all_features)
        )
    else:
        extra_feature_ratio = 0.0

    feature_similarity = max(
        0.0,
        feature_similarity
        - extra_feature_ratio * EXTRA_FEATURE_PENALTY
    )

    # ============================================================
    # 2. BOOLEAN MODIFIERS
    # ============================================================

    boolean_items = taxonomy.get("boolean_modifiers", [])

    boolean_score = 0.0
    boolean_max = 0.0

    for b in boolean_items:

        if b not in row.index:
            continue

        boolean_max += 1.0

        if _num(row[b]) >= 1:
            boolean_score += 1.0

    boolean_similarity = (
        boolean_score / boolean_max
        if boolean_max > 0
        else 0.0
    )

    # ============================================================
    # 3. APPLICATION TYPES
    # ============================================================

    app_types = taxonomy.get("application_types", [])

    app_score = 0.0
    app_max = 0.0

    for a in app_types:

        if a not in row.index:
            continue

        app_max += 1.0

        if _num(row[a]) >= 1:
            app_score += 1.0

    app_similarity = (
        app_score / app_max
        if app_max > 0
        else 0.0
    )

    # ============================================================
    # 4. TECHNOLOGY MODIFIERS
    # ============================================================

    technologies = taxonomy.get("technology_modifiers", [])

    tech_score = 0.0
    tech_max = 0.0

    for t in technologies:

        if t not in row.index:
            continue

        tech_max += 1.0

        if _num(row[t]) >= 1:
            tech_score += 1.0

    tech_similarity = (
        tech_score / tech_max
        if tech_max > 0
        else 0.0
    )

    # ============================================================
    # 5. ENUM MODIFIERS
    # ============================================================

    enum_modifiers = taxonomy.get("enum_modifiers") or {}

    enum_score = 0.0
    enum_max = 0.0

    for key, val in enum_modifiers.items():

        if key not in row.index:
            continue

        enum_max += 1.0

        if (
            str(row[key]).strip().lower()
            == str(val).strip().lower()
        ):
            enum_score += 1.0

    enum_similarity = (
        enum_score / enum_max
        if enum_max > 0
        else 0.0
    )

    # ============================================================
    # 6. FINAL WEIGHTED SCORE
    # ============================================================

    score = (
        feature_similarity * FEATURE_WEIGHT
        + boolean_similarity * BOOLEAN_WEIGHT
        + app_similarity * APP_TYPE_WEIGHT
        + tech_similarity * TECH_WEIGHT
        + enum_similarity * ENUM_WEIGHT
    )

    return score

from dataclasses import dataclass

@dataclass
class SimilarProject:
    final_budget: float
    final_budget_usd: float
    usd_rate: float

    min_budget: float
    max_budget: float
    min_budget_usd: float
    max_budget_usd: float

    organization: str
    outer_link: str
    scraped_project_id: str

    project_id: str
    title: str
    scraped_date_created: str
    description: str
    duration_days: float


def _to_similar_project_result(
    row: SimilarProject,
) -> dict:

    result = {}

    actual_price_tomans = row.final_budget

    converted_price_tomans = (
        row.final_budget_usd * float(row.usd_rate)
        if row.final_budget_usd and row.usd_rate
        else actual_price_tomans
    )

    converted_min_budget_tomans = (
        row.min_budget_usd * float(row.usd_rate)
        if row.min_budget_usd and row.usd_rate
        else row.min_budget
    )

    converted_max_budget_tomans = (
        row.max_budget_usd * float(row.usd_rate)
        if row.max_budget_usd and row.usd_rate
        else row.max_budget
    )

    project_link = ""

    if row.organization == "Karlancer":
        project_link = (
            "https://www.karlancer.com/projects/"
            + str(row.outer_link)
        )

    elif row.organization == "Ponisha":
        project_link = (
            "https://ponisha.ir/project/"
            + str(row.scraped_project_id)
        )

    result = {
        "id": str(row.project_id),
        "title": str(row.title),
        "date": str(row.scraped_date_created),
        "description": str(row.description),
        "timeline_days": int(row.duration_days or 0),

        "actual_price_tomans": actual_price_tomans,
        "converted_price_tomans": converted_price_tomans,

        "min_budget": row.min_budget,
        "max_budget": row.max_budget,

        "converted_min_budget_tomans": converted_min_budget_tomans,
        "converted_max_budget_tomans": converted_max_budget_tomans,

        "project_link": project_link,
    }

    return result
    

# ---------------------------------------------------------------------------
# Row -> SimilarProject mapping
# ---------------------------------------------------------------------------
def _to_similar_project(row: pd.Series, db: Session) -> dict:
    project = SimilarProject(
        final_budget=_num(row.get("final_budget")),
        final_budget_usd=_num(row.get("final_budget_usd")),
        usd_rate=get_current_usd_rate(db),

        min_budget=_num(row.get("budget_min")),
        max_budget=_num(row.get("budget_max")),
        min_budget_usd=_num(row.get("budget_min_usd")),
        max_budget_usd=_num(row.get("budget_max_usd")),

        organization=row.get("organization", ""),
    )

    return _to_similar_project_result(project)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def find_similar_projects(taxonomy: dict, db:Session , top_n: int = 10 , sendRaw = False) -> list[dict]:
    df = _load_dataset()
    if df.empty:
        return []

    # print("TAXONOMY:")
    # print(taxonomy)
    # print("FEATURES:")
    # print(taxonomy.get("features"))

    scores = df.apply(lambda row: _score_row(row, taxonomy), axis=1)
    ranked = df.assign(_match_score=scores).sort_values("_match_score", ascending=False)
    top = ranked.head(top_n)

    if sendRaw:
        return top.to_dict(orient="records")

    return [_to_similar_project(row , db) for _, row in top.iterrows()]