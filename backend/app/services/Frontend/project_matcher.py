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

# ---------------------------------------------------------------------------
# Scoring weights — tune these to change how much each dimension matters.
# ---------------------------------------------------------------------------
FEATURE_WEIGHT = 1.0    # multiplied by the project's own 0-5 score for that feature
BOOLEAN_WEIGHT = 3.0    # flat bonus if the boolean modifier is set (==1) on the project
APP_TYPE_WEIGHT = 4.0   # flat bonus if the application type matches
TECH_WEIGHT = 3.0       # flat bonus if the technology modifier matches
ENUM_WEIGHT = 2.0       # flat bonus for an exact enum match
NUMERIC_WEIGHT = 2.0    # scaled 0-1 by closeness, times this weight

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


def _score_row(row: pd.Series, taxonomy: dict) -> float:
    score = 0.0

    for feat in taxonomy.get("features", []):
        if feat in row.index:
            score += _num(row[feat]) * FEATURE_WEIGHT

    for b in taxonomy.get("boolean_modifiers", []):
        if b in row.index and _num(row[b]) >= 1:
            score += BOOLEAN_WEIGHT

    for a in taxonomy.get("application_types", []):
        if a in row.index and _num(row[a]) >= 1:
            score += APP_TYPE_WEIGHT

    for t in taxonomy.get("technology_modifiers", []):
        if t in row.index and _num(row[t]) >= 1:
            score += TECH_WEIGHT

    for key, val in (taxonomy.get("enum_modifiers") or {}).items():
        if key in row.index and str(row[key]).strip().lower() == str(val).strip().lower():
            score += ENUM_WEIGHT

    for key, target in (taxonomy.get("numeric_modifiers") or {}).items():
        if key not in row.index:
            continue
        actual = _num(row[key])
        target_val = _num(target)
        denom = max(abs(target_val), abs(actual), 1.0)
        closeness = 1.0 - min(abs(actual - target_val) / denom, 1.0)
        score += closeness * NUMERIC_WEIGHT

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

    scores = df.apply(lambda row: _score_row(row, taxonomy), axis=1)
    ranked = df.assign(_match_score=scores).sort_values("_match_score", ascending=False)
    top = ranked.head(top_n)

    if sendRaw:
        return top.to_dict(orient="records")

    return [_to_similar_project(row , db) for _, row in top.iterrows()]