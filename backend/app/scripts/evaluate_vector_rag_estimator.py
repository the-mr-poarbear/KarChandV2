"""
app/scripts/evaluate_vector_rag_estimator.py

Offline evaluation harness for the vector (Qdrant) RAG price estimator.

Same idea as evaluate_rag_estimator.py, but retrieval goes through the
Qdrant vector index (search() in retrieve_and_estimate.py) instead of the
taxonomy-tag scoring in project_matcher.py. build_index.py upserts every
row of full_dataset.csv into Qdrant — including the project we're about to
evaluate — so it will always come back as its own top hit. This script
over-fetches and drops the project out of its own comparables before
building the LLM prompt.

What it does, each run:
  1. Ensures the output CSV exists (creates it with headers if not).
  2. Reads project_ids already present in that CSV so re-runs resume
     instead of re-evaluating the same projects.
  3. Filters the dataset down to projects that actually have a price,
     then randomly samples from that pool.
  4. For each sampled project:
       - embeds its title+description as the query and searches Qdrant
       - drops any hit whose payload project_id matches the sampled
         project itself
       - calls the RAG estimator prompt (build_llm_prompt) with the
         remaining comparables to get an estimated price + reasoning
       - computes the project's own actual price converted to tomans
       - appends one row to the CSV: project_id, actual price, estimated
         price, reasoning
     Any skip (no price, empty description, no comparables, LLM/parse
     error) does NOT shrink the batch — the loop just keeps pulling the
     next project from the pool until it lands n_samples successes.
  5. Rotates across LLM_API_KEY / _2 / _3 / _4 round-robin, sleeping
     call_interval_seconds between calls to throttle request rate.
  6. Logs every attempt to a debug log file, and always overwrites a
     "last attempt" file with the full prompt/raw response/error of the
     most recent call so failures are easy to inspect.

Adjust the "ADJUST ME" import below to match your actual DB session
factory before running.

Usage:
    python -m app.scripts.evaluate_vector_rag_estimator --n 200 --top-k 10 --interval 2.0
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import logging
import random
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from openai import OpenAI
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.usd import get_current_usd_rate
from app.services.Frontend.project_matcher import _load_dataset, _num
from app.services.Frontend.extract_taxonomy import extract_json
from app.services.rag_system.retrieve_and_estimate import get_client, search, build_llm_prompt

# ADJUST ME: point this at your actual session factory
from app.core.database import SessionLocal


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
EVAL_DIR = Path(__file__).parent / "eval-rag"
OUTPUT_CSV = EVAL_DIR / "vector_rag_eval_results.csv"
LOG_FILE = EVAL_DIR / "vector_rag_eval_debug.log"
LAST_ATTEMPT_FILE = EVAL_DIR / "vector_rag_last_attempt.json"

CSV_FIELDS = ["project_id", "actual_price_tomans", "estimated_price_tomans", "reasoning"]

DEFAULT_N_SAMPLES = 200
DEFAULT_TOP_K = 10
DEFAULT_FETCH_BUFFER = 5   # extra hits pulled from Qdrant so top_k survive after dropping self
DEFAULT_CALL_INTERVAL_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Debug logging — a running log file (every attempt) + a "last attempt" file
# that always holds the full detail of the most recent call.
# ---------------------------------------------------------------------------
EVAL_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("vector_rag_eval")
logger.setLevel(logging.DEBUG)
if not logger.handlers:  # avoid duplicate handlers on re-import
    _file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_file_handler)
    logger.propagate = False


def _write_last_attempt(**fields) -> None:
    fields["timestamp"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(LAST_ATTEMPT_FILE, "w", encoding="utf-8") as f:
            json.dump(fields, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        logger.exception("failed to write last_attempt.json")


# ---------------------------------------------------------------------------
# LLM key rotation
# ---------------------------------------------------------------------------
LLM_KEYS = [
    settings.LLM_API_KEY,
    settings.LLM_API_KEY_2,
    settings.LLM_API_KEY_3,
    settings.LLM_API_KEY_4,
]
LLM_KEYS = [k for k in LLM_KEYS if k]  # drop unset/empty keys
if not LLM_KEYS:
    raise RuntimeError("No LLM API keys configured in settings.")

_key_cycle = itertools.cycle(LLM_KEYS)


def _next_client() -> OpenAI:
    key = next(_key_cycle)
    return OpenAI(api_key=key, base_url=settings.LLM_BASE_URL)


# ---------------------------------------------------------------------------
# CSV helpers (resumable)
# ---------------------------------------------------------------------------
def _ensure_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()


def _already_evaluated_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {row["project_id"] for row in reader if row.get("project_id")}


def _append_row(path: Path, row: dict) -> None:
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writerow(row)


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------
def _row_query_text(row: dict) -> str:
    title = str(row.get("title", "")).strip()
    desc = str(row.get("description", "")).strip()
    if title and desc:
        return f"{title}\n\n{desc}"
    return desc or title


def _has_valid_price(row: dict) -> bool:
    """True if this project has usable price data (either currency)."""
    final_budget = _num(row.get("final_budget"))
    final_budget_usd = _num(row.get("final_budget_usd"))
    return final_budget > 0 or final_budget_usd > 0


def _actual_price_tomans(row: dict, usd_rate: float) -> float:
    usd_rate = float(usd_rate)
    final_budget = _num(row.get("final_budget"))
    final_budget_usd = _num(row.get("final_budget_usd"))
    if final_budget_usd and usd_rate:
        return final_budget_usd * usd_rate
    return final_budget


# ---------------------------------------------------------------------------
# Retrieval — Qdrant vector search, self excluded from its own comparables
# ---------------------------------------------------------------------------
def _get_comparables(
    query: str,
    model: SentenceTransformer,
    client: QdrantClient,
    project_id: str,
    top_k: int,
    fetch_buffer: int,
):
    # Over-fetch: the sampled project is guaranteed to be in the index
    # (build_index.py upserted it) and will usually be its own top hit,
    # so pull extra results to still have top_k left after dropping it.
    raw_hits = search(query, model, client, top_k=top_k + fetch_buffer)
    filtered = [h for h in raw_hits if str(h.payload.get("project_id")) != str(project_id)]
    return filtered[:top_k]


# ---------------------------------------------------------------------------
# Estimation (key-rotated, rate-limited)
# ---------------------------------------------------------------------------
def _estimate_for_row(
    query: str,
    hits,
    usd_rate: float,
    call_interval_seconds: float,
    project_id: str,
) -> dict:
    prompt = build_llm_prompt(query, hits, usd_rate)

    client = _next_client()
    time.sleep(call_interval_seconds)

    response = client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = response.choices[0].message.content or ""

    logger.debug(
        "project_id=%s prompt_chars=%d raw_response=%r",
        project_id, len(prompt), content,
    )

    try:
        # extract_json tolerates markdown fences / preamble / trailing
        # text, which plain json.loads() does not.
        result = extract_json(content)
    except Exception as e:
        _write_last_attempt(
            project_id=project_id,
            stage="json_parse",
            prompt=prompt,
            raw_response=content,
            error=str(e),
            traceback=traceback.format_exc(),
        )
        logger.error("project_id=%s JSON parse failed: %s | raw=%r", project_id, e, content)
        raise RuntimeError(f"Could not parse LLM response as JSON: {e} | raw (first 300 chars): {content[:300]!r}") from e

    if not isinstance(result, dict):
        _write_last_attempt(
            project_id=project_id,
            stage="json_parse",
            prompt=prompt,
            raw_response=content,
            error=f"expected JSON object, got {type(result)}",
        )
        raise RuntimeError(f"Expected a JSON object from LLM, got {type(result)}")

    parsed = {
        "reasoning": str(result.get("reasoning", "")),
        "estimated_price": float(result.get("estimated_price", 0) or 0),
    }

    _write_last_attempt(
        project_id=project_id,
        stage="success",
        prompt=prompt,
        raw_response=content,
        parsed_result=parsed,
    )

    return parsed


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_evaluation(
    n_samples: int = DEFAULT_N_SAMPLES,
    top_k: int = DEFAULT_TOP_K,
    fetch_buffer: int = DEFAULT_FETCH_BUFFER,
    call_interval_seconds: float = DEFAULT_CALL_INTERVAL_SECONDS,
    seed: Optional[int] = None,
) -> None:
    if seed is not None:
        random.seed(seed)

    _ensure_csv(OUTPUT_CSV)
    done_ids = _already_evaluated_ids(OUTPUT_CSV)

    df = _load_dataset()
    if df.empty:
        print("Dataset is empty, nothing to evaluate.")
        return

    candidates = df[df["project_id"].astype(str).str.len() > 0]

    # Only ever choose projects that actually have a price.
    price_mask = candidates.apply(lambda r: _has_valid_price(r.to_dict()), axis=1)
    candidates = candidates[price_mask]

    candidate_ids = candidates["project_id"].astype(str).tolist()
    pool = [pid for pid in candidate_ids if pid not in done_ids]
    random.shuffle(pool)

    if not pool:
        print("Every priced project in the dataset is already in the eval CSV (or none have prices).")
        return

    n_samples = min(n_samples, len(pool))

    print(f"Loading embedding model: {settings.EMBEDDING_MODEL} ...")
    model = SentenceTransformer(settings.EMBEDDING_MODEL)
    qdrant_client = get_client()

    db: Session = SessionLocal()
    try:
        # get_current_usd_rate returns a Decimal — cast once here so every
        # downstream multiplication against floats doesn't blow up with
        # "unsupported operand type(s) for *: 'float' and 'decimal.Decimal'"
        usd_rate = float(get_current_usd_rate(db))

        successes = 0
        attempted = 0

        for project_id in pool:
            if successes >= n_samples:
                break
            if project_id in done_ids:
                continue  # guard against dupes within the same pool

            attempted += 1
            row = candidates[candidates["project_id"].astype(str) == project_id].iloc[0].to_dict()

            if not _has_valid_price(row):
                print(f"[{successes}/{n_samples}] skipping {project_id}: no price data, fetching another project")
                continue

            query = _row_query_text(row)
            if not query:
                print(f"[{successes}/{n_samples}] skipping {project_id}: empty description, fetching another project")
                continue

            print(f"[{successes + 1}/{n_samples}] evaluating project_id={project_id} (attempt {attempted})")

            try:
                hits = _get_comparables(query, model, qdrant_client, project_id, top_k, fetch_buffer)
                if not hits:
                    print(f"  no comparables found for {project_id}, fetching another project")
                    continue

                result = _estimate_for_row(query, hits, usd_rate, call_interval_seconds, project_id)
                actual = _actual_price_tomans(row, usd_rate)

                _append_row(
                    OUTPUT_CSV,
                    {
                        "project_id": project_id,
                        "actual_price_tomans": actual,
                        "estimated_price_tomans": result["estimated_price"],
                        "reasoning": result["reasoning"],
                    },
                )
                done_ids.add(project_id)
                successes += 1

            except Exception as e:
                logger.error("project_id=%s failed: %s\n%s", project_id, e, traceback.format_exc())
                _write_last_attempt(
                    project_id=project_id,
                    stage="unhandled_error",
                    error=str(e),
                    traceback=traceback.format_exc(),
                )
                print(f"  failed on {project_id}: {e}  (see {LOG_FILE.name} / {LAST_ATTEMPT_FILE.name}) — fetching another project")
                continue

        if successes < n_samples:
            print(f"Pool exhausted: only found {successes}/{n_samples} evaluable priced projects.")
    finally:
        db.close()

    print(f"Done. Results in {OUTPUT_CSV}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate the vector (Qdrant) RAG price estimator against sampled dataset projects.")
    parser.add_argument("--n", type=int, default=DEFAULT_N_SAMPLES, help="number of projects to evaluate this run")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="number of comparables per query")
    parser.add_argument("--fetch-buffer", type=int, default=DEFAULT_FETCH_BUFFER, help="extra hits fetched from Qdrant so top_k survive after dropping self")
    parser.add_argument("--interval", type=float, default=DEFAULT_CALL_INTERVAL_SECONDS, help="seconds between LLM calls")
    parser.add_argument("--seed", type=int, default=None, help="random seed for reproducible sampling")
    args = parser.parse_args()

    run_evaluation(
        n_samples=args.n,
        top_k=args.top_k,
        fetch_buffer=args.fetch_buffer,
        call_interval_seconds=args.interval,
        seed=args.seed,
    )
