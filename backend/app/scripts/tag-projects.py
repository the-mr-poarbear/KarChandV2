"""
Tags each project in the database with features and modifiers from the
taxonomy, using an LLM. Outputs a CSV with one row per project and one
column per feature/modifier.

Usage:
    python -m app.scripts.tag_projects
    python -m app.scripts.tag_projects --organization Karlancer
    python -m app.scripts.tag_projects --limit 500
    python -m app.scripts.tag_projects --para 4
    python -m app.scripts.tag_projects --para 4 --local-para 2

PARALLELISM MODEL
------------------
--para N          Splits your 4 API keys into N groups. Each group gets
                   its own private key subset and only rotates within it
                   on rate limit (e.g. --para 2 -> group 0 uses
                   [KEY_1, KEY_2], group 1 uses [KEY_3, KEY_4]). If 4
                   doesn't divide evenly by N (e.g. --para 3), each group
                   still starts on a different key, but falls back to
                   cycling through ALL 4 keys linearly on rate limit.

--local-para M     Within EACH group, spawns M sub-processes that all
                   share that group's key pool and run concurrently
                   (each with its own independent throttle timer). Total
                   OS processes launched = N * M.

All workers, no matter how many, read/write ONE shared tagged_projects.csv
and ONE shared progress.json (both lock-protected across processes, AND
retried on transient Windows file-lock errors like WinError 5). Batches
are split round-robin across all N*M workers by batch index.
progress.json records exactly how many batches each individual worker
slot has completed, so a later run — even with a different
--para/--local-para — can resume without re-tagging anything. As a
safety net independent of that bookkeeping, every worker also skips any
project_id it finds is already present in tagged_projects.csv before
tagging it.

The taxonomy (features, modifiers, descriptions, rules) is defined in
taxonomy.json — edit that file to tune without touching this script.
"""
import argparse
import csv
import json
import multiprocessing
import random
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.project import Project
from app.models.category import Category
from app.models.organization import Organization

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BATCH_SIZE = 5
MAX_RETRIES = 3
MIN_SECONDS_BETWEEN_CALLS = 6.5
MODEL = settings.LLM_MODEL

STATE_DIR = Path("tagging_state")
PROGRESS_PATH = STATE_DIR / "progress.json"
OUTPUT_PATH = STATE_DIR / "tagged_projects.csv"
TAXONOMY_PATH = Path(__file__).parent.parent / "taxonomy/taxonomy.json"

# Windows can transiently throw PermissionError / WinError 5 when a file
# is briefly held by AV, indexing, or cloud-sync (OneDrive etc.) at the
# exact moment we try to replace/append it. These control the retry.
FILE_LOCK_MAX_ATTEMPTS = 10
FILE_LOCK_BASE_DELAY = 0.05

ALL_KEYS = [
    settings.LLM_API_KEY,
    settings.LLM_API_KEY_2,
    settings.LLM_API_KEY_3,
    settings.LLM_API_KEY_4,
]

# Per-process globals. Each spawned process gets its OWN copy of these
# (multiprocessing uses separate memory), so there's no cross-process
# collision even though the names look "global".
client = None
KEY_POOL: list[str] = []
_key_index = 0
_last_call_time = 0.0
_call_durations: list[float] = []


# ---------------------------------------------------------------------------
# Key pool splitting
# ---------------------------------------------------------------------------
def get_key_pools(para: int) -> list[list[str]]:
    """
    Splits ALL_KEYS into `para` pools, one per key-group.

    - If len(ALL_KEYS) divides evenly by `para` (para in 1, 2, 4): each
      group gets a private, non-overlapping chunk of keys.
    - Otherwise (e.g. para=3): an even split isn't possible, so each
      group starts on a different key, but its rate-limit fallback
      cycles through the FULL 4-key list in a fixed linear order.
    """
    n = len(ALL_KEYS)
    if para <= 0:
        raise ValueError("--para must be >= 1")
    if n % para == 0:
        chunk = n // para
        return [ALL_KEYS[i * chunk:(i + 1) * chunk] for i in range(para)]
    return [ALL_KEYS[i % n:] + ALL_KEYS[:i % n] for i in range(para)]


def init_worker_client(key_pool: list[str]):
    """Must be called once at the start of every worker process."""
    global client, KEY_POOL, _key_index
    KEY_POOL = key_pool
    _key_index = 0
    client = OpenAI(base_url=settings.LLM_BASE_URL, api_key=KEY_POOL[0])


# ---------------------------------------------------------------------------
# File-lock-resilient write helper
# ---------------------------------------------------------------------------
def _retry_on_file_lock(fn, *, description: str = "file operation"):
    """
    Retries a file operation that can transiently fail with
    PermissionError (WinError 5) or OSError when another process, AV
    tool, or cloud-sync client briefly holds a handle on the file.
    Uses exponential backoff with jitter. Raises the last error if all
    attempts are exhausted.
    """
    last_error = None
    for attempt in range(1, FILE_LOCK_MAX_ATTEMPTS + 1):
        try:
            return fn()
        except (PermissionError, OSError) as e:
            last_error = e
            if attempt == FILE_LOCK_MAX_ATTEMPTS:
                break
            delay = FILE_LOCK_BASE_DELAY * (2 ** (attempt - 1)) + random.uniform(0, 0.05)
            delay = min(delay, 3.0)
            print(f"   ⏳ {description} locked (attempt {attempt}/{FILE_LOCK_MAX_ATTEMPTS}), "
                  f"retrying in {delay:.2f}s: {e}")
            time.sleep(delay)
    raise RuntimeError(f"{description} failed after {FILE_LOCK_MAX_ATTEMPTS} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Load taxonomy from JSON - single source of truth
# ---------------------------------------------------------------------------
def load_taxonomy(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_derived(tx: dict) -> dict:
    features        = tx["features"]["values"]
    app_types       = tx["application_types"]["values"]
    app_definitions = tx["application_types"].get("definitions", {})
    tech_mods       = tx["technology_modifiers"]["values"]
    numeric_fields  = tx["numeric_modifiers"]["fields"]
    boolean_fields  = tx["boolean_modifiers"]["fields"]
    enum_fields     = tx["enum_modifiers"]["fields"]
    rules           = tx["prompt_rules"]["rules"]

    csv_columns = (
        ["project_id", "title", "category", "organization", "created_at"]
        + features + app_types + tech_mods
        + list(numeric_fields.keys())
        + list(boolean_fields.keys())
        + list(enum_fields.keys())
    )

    numeric_prompt_lines = "\n".join(f"- {name}: {desc}" for name, desc in numeric_fields.items())
    boolean_prompt_lines = "\n".join(f"- {name}: {desc}" for name, desc in boolean_fields.items())
    enum_prompt_lines = "\n".join(
        f"- {name} ({' | '.join(info['values'])}): {info['description']}"
        for name, info in enum_fields.items()
    )
    app_def_lines = "\n".join(f'- "{k}": {v}' for k, v in app_definitions.items()) if app_definitions else ""

    enum_defaults = {name: info["values"][0] for name, info in enum_fields.items()}
    boolean_defaults = {name: 0 for name in boolean_fields}
    numeric_defaults = {name: 0 for name in numeric_fields}

    system_prompt = f"""You are a senior software architect analyzing freelance project requirements.

You will receive a batch of projects. For each project, return its feature vector based on
this taxonomy.

FEATURES (0–5 confidence scale — 0 if absent, 1 if weak signal/barely implied, 2 if mentioned but minor,
3 if explicitly required but not central, 4 if clearly required, 5 if a defining feature of the project):
{json.dumps(features, ensure_ascii=False)}

APPLICATION TYPE (binary — 1 if the project belongs to this type, 0 if not; multiple types allowed):
{json.dumps(app_types, ensure_ascii=False)}
{("Clarifications:\n" + app_def_lines) if app_def_lines else ""}

TECHNOLOGY MODIFIERS (binary — 1 if the project explicitly uses this platform/tech, 0 if not):
{json.dumps(tech_mods, ensure_ascii=False)}

NUMERIC MODIFIERS (always return all keys; use 0 only if truly none detectable — always estimate):
{numeric_prompt_lines}

BOOLEAN MODIFIERS (1 or 0 — always return all keys):
{boolean_prompt_lines}

ENUM MODIFIERS (pick exactly one value from the allowed list — always return all keys):
{enum_prompt_lines}

Return a JSON array with one object per project, in this exact shape:
{{
  "project_id": "<the project_id you were given>",
  "features": {{"<feature_name>": <0-5>, ...}},
  "application_type": {{"<type>": 1, ...}},
  "technology_modifiers": {{"<tech>": 1, ...}},
  "numeric_modifiers": {json.dumps(numeric_defaults)},
  "boolean_modifiers": {json.dumps(boolean_defaults)},
  "enum_modifiers": {json.dumps(enum_defaults)}
}}

Rules:
{"".join(f"- {r}{chr(10)}" for r in rules)}"""

    return {
        "features": features,
        "app_types": app_types,
        "tech_mods": tech_mods,
        "numeric_fields": list(numeric_fields.keys()),
        "boolean_fields": list(boolean_fields.keys()),
        "enum_fields": list(enum_fields.keys()),
        "enum_allowed": {name: info["values"] for name, info in enum_fields.items()},
        "csv_columns": csv_columns,
        "system_prompt": system_prompt,
    }


_raw_taxonomy = load_taxonomy(TAXONOMY_PATH)
TAXONOMY = build_derived(_raw_taxonomy)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def load_json(path: Path, default):
    if not path.exists():
        return default

    def _read():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    return _retry_on_file_lock(_read, description=f"reading {path.name}")


def save_json(path: Path, data):
    STATE_DIR.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")

    def _write_and_replace():
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    _retry_on_file_lock(_write_and_replace, description=f"writing {path.name}")


def extract_json(raw_text: str):
    """Robust extraction: handles fences, preamble text, and trailing prose."""
    text = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    if start == -1:
        raise json.JSONDecodeError("No JSON array or object found", text, 0)

    open_char = text[start]
    close_char = "]" if open_char == "[" else "}"
    depth, end, in_string, escape = 0, -1, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == open_char:
            depth += 1
        elif c == close_char:
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        raise json.JSONDecodeError("No matching closing bracket found", text, start)
    return json.loads(text[start:end])


def load_tagged_ids(path: Path = OUTPUT_PATH) -> set[str]:
    """
    Safety net used at the start of every run/worker: whatever the
    progress.json bookkeeping says, NEVER re-tag a project_id that's
    already sitting in the output CSV.
    """
    if not path.exists():
        return set()

    def _read():
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return {row["project_id"] for row in reader if row.get("project_id")}

    return _retry_on_file_lock(_read, description=f"reading {path.name}")


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------
def _record_call_duration(duration: float, worker_tag: str = ""):
    global _call_durations
    _call_durations.append(duration)
    avg = sum(_call_durations) / len(_call_durations)
    prefix = f"[{worker_tag}] " if worker_tag else ""
    print(f"{prefix}⏱  response took {duration:.2f}s (avg over {len(_call_durations)} calls: {avg:.2f}s)")


def _save_call_timing(worker_id: str):
    save_json(STATE_DIR / f"call_timing_{worker_id}.json", {
        "count": len(_call_durations),
        "total_seconds": sum(_call_durations),
        "average_seconds": (sum(_call_durations) / len(_call_durations)) if _call_durations else 0,
        "durations": _call_durations,
    })


def _print_aggregate_timing(worker_ids: list[str]):
    all_durations = []
    for wid in worker_ids:
        data = load_json(STATE_DIR / f"call_timing_{wid}.json", default=None)
        if data and data.get("durations"):
            all_durations.extend(data["durations"])
    if not all_durations:
        return
    avg = sum(all_durations) / len(all_durations)
    print(f"\n⏱  Aggregate across {len(worker_ids)} worker(s): {len(all_durations)} calls, "
          f"avg response time {avg:.2f}s, total LLM time {sum(all_durations):.2f}s")


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------
def call_llm(user_content: str, worker_tag: str = "") -> str:
    global _last_call_time, _key_index
    elapsed = time.monotonic() - _last_call_time
    if elapsed < MIN_SECONDS_BETWEEN_CALLS:
        time.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)

    prefix = f"[{worker_tag}] " if worker_tag else ""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"{prefix}🚀  Calling LLM (attempt {attempt}/{MAX_RETRIES})")
        request_start = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=MODEL,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": TAXONOMY["system_prompt"]},
                    {"role": "user", "content": user_content},
                ],
            )
            duration = time.monotonic() - request_start
            _record_call_duration(duration, worker_tag=worker_tag)

            print(f"{prefix}LLM call successful")
            _last_call_time = time.monotonic()
            result = response.choices[0].message.content or ""

            STATE_DIR.mkdir(exist_ok=True)
            suffix = f"_{worker_tag}" if worker_tag else ""

            def _write_debug_files():
                (STATE_DIR / f"last_request{suffix}.txt").write_text(
                    f"=== SYSTEM ===\n{TAXONOMY['system_prompt']}\n\n=== USER ===\n{user_content}",
                    encoding="utf-8",
                )
                (STATE_DIR / f"last_response{suffix}.txt").write_text(result, encoding="utf-8")

            try:
                _retry_on_file_lock(_write_debug_files, description="writing debug files")
            except RuntimeError as debug_write_error:
                # Debug files are nice-to-have, not critical — don't fail
                # the whole batch just because these couldn't be written.
                print(f"{prefix}⚠️  Could not write debug files (non-fatal): {debug_write_error}")

            return result
        except Exception as e:
            duration = time.monotonic() - request_start
            print(f"{prefix}exception in call_llm after {duration:.2f}s:", e)
            last_error = e

            _key_index = (_key_index + 1) % len(KEY_POOL)
            client.api_key = KEY_POOL[_key_index]
            print(f"{prefix}⚠️  Switching to key #{_key_index + 1}/{len(KEY_POOL)} in this worker's pool.")

    raise RuntimeError(f"API call failed after {MAX_RETRIES} attempts: {last_error}")


def flatten_tagged(tagged: dict, project: dict) -> dict:
    row = {
        "project_id": project["project_id"],
        "title": project["title"],
        "category": project["category"],
        "organization": project["organization"],
        "created_at": project["created_at"],
    }

    features_present = tagged.get("features", {})
    for f in TAXONOMY["features"]:
        row[f] = features_present.get(f, 0)

    app_types_present = tagged.get("application_type", {})
    for t in TAXONOMY["app_types"]:
        row[t] = app_types_present.get(t, 0)

    tech_present = tagged.get("technology_modifiers", {})
    for t in TAXONOMY["tech_mods"]:
        row[t] = tech_present.get(t, 0)

    numeric = tagged.get("numeric_modifiers", {})
    for k in TAXONOMY["numeric_fields"]:
        row[k] = numeric.get(k, 0)

    boolean = tagged.get("boolean_modifiers", {})
    for k in TAXONOMY["boolean_fields"]:
        row[k] = boolean.get(k, 0)

    enum_out = tagged.get("enum_modifiers", {})
    for k in TAXONOMY["enum_fields"]:
        val = enum_out.get(k, "")
        allowed = TAXONOMY["enum_allowed"].get(k, [])
        if allowed and val not in allowed:
            print(f"   ⚠️  Unexpected enum value for '{k}': '{val}' — defaulting to '{allowed[0]}'")
            val = allowed[0]
        row[k] = val

    return row


def tag_batch(batch: list[dict], worker_tag: str = "") -> dict:
    user_content = (
        f"Tag these {len(batch)} projects:\n\n"
        + json.dumps(
            [
                {
                    "project_id": p["project_id"],
                    "title": p["title"],
                    "description": p["description"][:800],
                    "skills": p.get("skills", []),
                }
                for p in batch
            ],
            ensure_ascii=False,
        )
    )

    raw = call_llm(user_content, worker_tag=worker_tag)

    try:
        tagged_list = extract_json(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse LLM output: {e}\nRaw (first 500 chars):\n{raw[:500]}")

    if not isinstance(tagged_list, list):
        raise RuntimeError(f"Expected JSON array, got {type(tagged_list)}")

    return {item["project_id"]: item for item in tagged_list if "project_id" in item}


# ---------------------------------------------------------------------------
# Database fetch
# ---------------------------------------------------------------------------
def fetch_projects(
    after_created_at: str | None,
    organization_name: str | None,
    limit: int | None,
) -> list[dict]:
    from app.models.project_skill import ProjectSkill
    from app.models.skill import Skill

    db = SessionLocal()
    try:
        query = (
            db.query(
                Project.id,
                Project.title,
                Project.description,
                Project.created_at,
                Category.name.label("category"),
                Organization.name.label("organization"),
            )
            .join(Category, Project.category_id == Category.id)
            .join(Organization, Category.organization_id == Organization.id)
            .filter(Project.description.isnot(None))
        )

        if organization_name:
            query = query.filter(Organization.name == organization_name)

        if after_created_at:
            query = query.filter(
                Project.created_at > datetime.fromisoformat(after_created_at)
            )

        query = query.order_by(Project.created_at.asc())

        if limit:
            query = query.limit(limit)

        rows = query.all()
        if not rows:
            return []

        project_ids = [str(r.id) for r in rows]
        skill_rows = (
            db.query(ProjectSkill.project_id, Skill.name)
            .join(Skill, ProjectSkill.skill_id == Skill.id)
            .filter(ProjectSkill.project_id.in_(project_ids))
            .all()
        )

        skills_by_project: dict[str, list[str]] = {}
        for project_id, skill_name in skill_rows:
            skills_by_project.setdefault(str(project_id), []).append(skill_name)

        return [
            {
                "project_id": str(r.id),
                "title": r.title or "",
                "description": r.description or "",
                "category": r.category or "",
                "organization": r.organization or "",
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "skills": skills_by_project.get(str(r.id), []),
            }
            for r in rows
        ]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Shared-file writers (cross-process lock + Windows-file-lock retry)
# ---------------------------------------------------------------------------
def append_rows_locked(rows: list[dict], csv_lock):
    if not rows:
        return
    with csv_lock:
        STATE_DIR.mkdir(exist_ok=True)

        def _write():
            first_write = not OUTPUT_PATH.exists()
            with open(OUTPUT_PATH, "w" if first_write else "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=TAXONOMY["csv_columns"])
                if first_write:
                    writer.writeheader()
                writer.writerows(rows)

        _retry_on_file_lock(_write, description=f"writing {OUTPUT_PATH.name}")


def update_progress_locked(progress_lock, worker_id: str, para: int, local_para: int,
                            rows_written: int, my_local_batch_count: int):
    with progress_lock:
        progress = load_json(PROGRESS_PATH, default={
            "last_created_at": None,
            "projects_tagged": 0,
            "batches_completed": 0,
            "last_run_para_degree": para,
            "last_run_local_para_degree": local_para,
            "process_batch_progress": {},
        })
        progress.setdefault("process_batch_progress", {})
        progress["process_batch_progress"][worker_id] = my_local_batch_count
        progress["projects_tagged"] = progress.get("projects_tagged", 0) + rows_written
        progress["batches_completed"] = progress.get("batches_completed", 0) + 1
        progress["last_run_para_degree"] = para
        progress["last_run_local_para_degree"] = local_para
        save_json(PROGRESS_PATH, progress)


def finalize_progress_locked(progress_lock, para: int, local_para: int, newest_created_at: str | None):
    """
    Called once, only after ALL workers in the session finish their full
    batch list. Advances the real 'last_created_at' cursor (so the next
    invocation's DB query starts later) and clears the per-worker
    in-session counters, since they're meaningless once the session is done.
    """
    with progress_lock:
        progress = load_json(PROGRESS_PATH, default={
            "last_created_at": None, "projects_tagged": 0, "batches_completed": 0,
        })
        if newest_created_at:
            progress["last_created_at"] = newest_created_at
        progress["last_run_para_degree"] = para
        progress["last_run_local_para_degree"] = local_para
        progress["process_batch_progress"] = {}
        save_json(PROGRESS_PATH, progress)


# ---------------------------------------------------------------------------
# Worker process entry point
# ---------------------------------------------------------------------------
def worker_loop(worker_id: str, key_pool: list[str], my_batches: list[list[dict]],
                 resume_from_local_idx: int, already_tagged_ids: set[str],
                 csv_lock, progress_lock, para: int, local_para: int):
    """
    Runs inside its own OS process. `worker_id` is a string like "0_1"
    (group 0, local sub-worker 1) that uniquely identifies this slot for
    logging, progress tracking, and per-worker file naming.
    """
    prefix = f"[{worker_id}] "
    init_worker_client(key_pool)

    remaining = my_batches[resume_from_local_idx:]
    print(f"{prefix}assigned {len(my_batches)} batch(es) total, resuming from local batch #{resume_from_local_idx} "
          f"({len(remaining)} left)")

    try:
        for offset, batch in enumerate(remaining):
            local_batch_num = resume_from_local_idx + offset + 1

            # Safety-net dedup — covers the case where progress.json's
            # bookkeeping doesn't line up cleanly (e.g. --para changed).
            batch = [p for p in batch if p["project_id"] not in already_tagged_ids]
            if not batch:
                update_progress_locked(progress_lock, worker_id, para, local_para, 0, local_batch_num)
                continue

            print(f"\n{prefix}--- local batch {local_batch_num}/{len(my_batches)} ({len(batch)} projects) ---")
            for p in batch:
                print(f"{prefix}   {p['title'][:60]}")

            try:
                tagged_by_id = tag_batch(batch, worker_tag=worker_id)
            except Exception as e:
                print(f"{prefix}❌ batch failed permanently: {e}")
                print(f"{prefix}   Progress NOT advanced for this batch. Re-run to retry.")
                raise

            rows = []
            for p in batch:
                pid = p["project_id"]
                if pid not in tagged_by_id:
                    print(f"{prefix}   ⚠️  LLM did not return a result for {pid} ({p['title'][:40]}) — skipping")
                    continue
                rows.append(flatten_tagged(tagged_by_id[pid], p))
                already_tagged_ids.add(pid)

            append_rows_locked(rows, csv_lock)
            update_progress_locked(progress_lock, worker_id, para, local_para, len(rows), local_batch_num)
            print(f"{prefix}   ✅ {len(rows)} rows written")
    finally:
        _save_call_timing(worker_id)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------
def run(organization_name: str | None, limit: int | None, para: int = 1, local_para: int = 1):
    progress = load_json(PROGRESS_PATH, default={
        "last_created_at": None,
        "projects_tagged": 0,
        "batches_completed": 0,
        "last_run_para_degree": para,
        "last_run_local_para_degree": local_para,
        "process_batch_progress": {},
    })

    after_created_at = progress.get("last_created_at")
    prev_para = progress.get("last_run_para_degree")
    prev_local_para = progress.get("last_run_local_para_degree", 1)
    per_worker_progress = progress.get("process_batch_progress", {}) or {}

    if per_worker_progress and (prev_para != para or prev_local_para != local_para):
        print(f"⚠️  Previous incomplete run used --para {prev_para} --local-para {prev_local_para}, "
              f"this run uses --para {para} --local-para {local_para}. Batch-to-worker assignment "
              f"changes with these settings, so this session's per-worker resume counters restart "
              f"from zero — but the CSV dedup check still guarantees no project is ever tagged twice.")
        per_worker_progress = {}

    if after_created_at:
        print(f"↻ Resuming after {after_created_at} ({progress.get('projects_tagged', 0)} projects already tagged)")

    all_projects = fetch_projects(after_created_at, organization_name, limit)
    if not all_projects:
        print("✅ Nothing to do.")
        return

    batches = [all_projects[i:i + BATCH_SIZE] for i in range(0, len(all_projects), BATCH_SIZE)]
    newest_created_at = all_projects[-1]["created_at"]
    total_workers = para * local_para

    print(f"📦 {len(all_projects)} projects ({len(batches)} batches) to tag"
          + (f" (filtered to {organization_name})" if organization_name else ""))

    already_tagged_ids = load_tagged_ids()

    # -------------------------------------------------------------------
    # Single process
    # -------------------------------------------------------------------
    if total_workers == 1:
        manager = multiprocessing.Manager()
        csv_lock = manager.Lock()
        progress_lock = manager.Lock()
        resume_from = int(per_worker_progress.get("0_0", 0))
        worker_loop("0_0", ALL_KEYS, batches, resume_from, set(already_tagged_ids),
                    csv_lock, progress_lock, para, local_para)
        finalize_progress_locked(progress_lock, para, local_para, newest_created_at)
        _print_aggregate_timing(["0_0"])
        print(f"\n✅ Done. → {OUTPUT_PATH}")
        return

    # -------------------------------------------------------------------
    # Multi-process: para groups × local_para sub-workers each
    # -------------------------------------------------------------------
    key_pools = get_key_pools(para)
    slots = [(g, l) for g in range(para) for l in range(local_para)]  # index = global slot
    worker_ids = [f"{g}_{l}" for (g, l) in slots]

    worker_batches = [
        [b for i, b in enumerate(batches) if i % total_workers == slot_idx]
        for slot_idx in range(total_workers)
    ]

    manager = multiprocessing.Manager()
    csv_lock = manager.Lock()
    progress_lock = manager.Lock()

    print(f"🧵 Launching {para} group(s) × {local_para} local worker(s) = {total_workers} process(es)")
    procs = []
    for slot_idx, (g, l) in enumerate(slots):
        wid = worker_ids[slot_idx]
        resume_from = int(per_worker_progress.get(wid, 0))
        print(f"   worker {wid}: {len(worker_batches[slot_idx])} batch(es), resuming from local #{resume_from}, "
              f"group {g} keys starting with ...{key_pools[g][0][-4:]}")

        p = multiprocessing.Process(
            target=worker_loop,
            args=(wid, key_pools[g], worker_batches[slot_idx], resume_from,
                  set(already_tagged_ids), csv_lock, progress_lock, para, local_para),
        )
        p.start()
        procs.append(p)

    failed = []

    def _handle_sigint(signum, frame):
        print("\n🛑 Interrupt received — stopping all workers. Progress is saved incrementally "
              "after every batch, so it's safe to resume later (same or different --para/--local-para).")
        for p in procs:
            if p.is_alive():
                p.terminate()
        for p in procs:
            p.join()
        _print_aggregate_timing(worker_ids)
        sys.exit(130)

    old_handler = signal.signal(signal.SIGINT, _handle_sigint)
    try:
        for p in procs:
            p.join()
        exit_codes = [p.exitcode for p in procs]
        failed = [worker_ids[i] for i, code in enumerate(exit_codes) if code != 0]

        if failed:
            print(f"⚠️  Worker(s) {failed} exited with errors. Progress up to each worker's last "
                  f"completed batch is saved; re-run to retry the rest.")
        else:
            print("✅ All workers finished cleanly.")
            finalize_progress_locked(progress_lock, para, local_para, newest_created_at)
    finally:
        signal.signal(signal.SIGINT, old_handler)
        _print_aggregate_timing(worker_ids)

    print(f"\n✅ Done. → {OUTPUT_PATH}")
    if failed:
        raise RuntimeError(f"Worker(s) {failed} failed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", type=str, default=None,
                        help="Filter to one organization by name (e.g. 'Karlancer')")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max number of projects to tag in this run (total, across all workers)")
    parser.add_argument("--para", type=int, default=1,
                        help="Number of API-key groups to split work across (top-level parallelism)")
    parser.add_argument("--local-para", type=int, default=1,
                        help="Number of sub-processes PER key group, sharing that group's keys concurrently")
    args = parser.parse_args()

    try:
        run(args.organization, args.limit, para=args.para, local_para=args.local_para)
    except KeyboardInterrupt:
        print("\n🛑 Interrupted — partial progress already saved.")
        sys.exit(130)