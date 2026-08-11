# app/services/price_estimator.py
import json
from pathlib import Path
from threading import Lock

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

MODEL_DIR = Path(__file__).parent.parent.parent
MODEL_PATH = MODEL_DIR / "catboost_price_model.cbm"
SCHEMA_PATH = MODEL_DIR / "catboost_price_model_schema.json"

_model: CatBoostRegressor | None = None
_schema: dict | None = None
_lock = Lock()


def _load():
    global _model, _schema
    if _model is not None:
        return _model, _schema
    with _lock:
        if _model is not None:
            return _model, _schema
        model = CatBoostRegressor()
        model.load_model(str(MODEL_PATH))
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema = json.load(f)
        _model, _schema = model, schema
        return _model, _schema


def _build_feature_row(taxonomy: dict, schema: dict, duration_days: float = 30) -> pd.DataFrame:
    columns = schema["columns"]
    row = {col: 0 for col in columns}

    if "duration_days" in row:
        row["duration_days"] = duration_days

    for feat in taxonomy.get("features", []):
        if feat in row:
            row[feat] = 1

    if "feature_count" in row:
        row["feature_count"] = len(taxonomy.get("features", []))

    for b in taxonomy.get("boolean_modifiers", []):
        if b in row:
            row[b] = 1

    for t in taxonomy.get("technology_modifiers", []):
        if t in row:
            row[t] = 1

    enum_modifiers = taxonomy.get("enum_modifiers") or {}

    # Any ordinal columns this particular model was trained with — none
    # right now, but this loop stays correct automatically if a future
    # retrain reintroduces some.
    for col in schema.get("ordinal_cols", []):
        order = schema["enum_order"][col]
        val = str(enum_modifiers.get(col, "")).strip().lower()
        row[col] = order.index(val) if val in order else -1

    # project_type: one-hot, restricted to categories seen in training
    project_type = str(enum_modifiers.get("project_type", "")).strip().lower()
    prefix = schema.get("project_type_prefix", "project_type_")
    dummy_col = f"{prefix}{project_type}"
    if dummy_col in schema.get("project_type_dummy_cols", []) and dummy_col in row:
        row[dummy_col] = 1

    for key, val in (taxonomy.get("numeric_modifiers") or {}).items():
        if key in row:
            row[key] = val

    return pd.DataFrame([row], columns=columns)


def estimate_price(taxonomy: dict, duration_days: float = 30) -> float:
    model, schema = _load()
    X = _build_feature_row(taxonomy, schema, duration_days=duration_days)
    pred_log = model.predict(X)[0]
    return float(np.expm1(pred_log)) if schema.get("target_transform") == "log1p" else float(pred_log)