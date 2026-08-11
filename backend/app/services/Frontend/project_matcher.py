# app/services/project_matcher.py
"""
Scores every project in full_dataset.csv against a taxonomy (features,
modifiers, application types, etc.) and returns the closest matches.
"""
from pathlib import Path
from threading import Lock
from typing import Optional

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


# ---------------------------------------------------------------------------
# Row -> SimilarProject mapping
# ---------------------------------------------------------------------------
def _to_similar_project(row: pd.Series) -> dict:
    final_budget = _num(row.get("final_budget"))
    final_budget_usd = _num(row.get("final_budget_usd"))
    usd_rate = _num(row.get("usd_rate"))

    # actual_price_tomans: the project's own budget as scraped (native currency).
    actual_price_tomans = final_budget

    # converted_price_tomans: the USD-normalized budget converted back to
    # tomans via that project's recorded usd_rate, so projects from
    # different platforms/currencies land on one comparable scale.
    # Falls back to the raw budget if either input is missing.
    converted_price_tomans = (
        final_budget_usd * usd_rate if final_budget_usd and usd_rate else actual_price_tomans
    )

    return {
        "id": str(row.get("project_id", "")),
        "title": str(row.get("title", "")),
        "date": str(row.get("created_at", "")),
        "description": str(row.get("description", "")),
        "timeline_days": int(_num(row.get("duration_days"))),
        "actual_price_tomans": actual_price_tomans,
        "converted_price_tomans": converted_price_tomans,
        "project_link": str(row.get("outer_link", "")),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def find_similar_projects(taxonomy: dict, top_n: int = 10) -> list[dict]:
    df = _load_dataset()
    if df.empty:
        return []

    scores = df.apply(lambda row: _score_row(row, taxonomy), axis=1)
    ranked = df.assign(_match_score=scores).sort_values("_match_score", ascending=False)
    top = ranked.head(top_n)

    return [_to_similar_project(row) for _, row in top.iterrows()]