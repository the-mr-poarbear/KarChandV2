"""
Tags each project in the database with features and modifiers from the
taxonomy, using an LLM. Outputs a CSV with one row per project and one
column per feature/modifier.

Usage:
    python -m app.scripts.tag_projects
    python -m app.scripts.tag_projects --organization Karlancer
    python -m app.scripts.tag_projects --limit 500
    python -m app.scripts.tag_projects --para 2

Resumes automatically if interrupted — projects are processed in
created_at order, and progress tracks the last successfully tagged
project's created_at timestamp, so a resume just continues from there.

With --para N > 1, work is sharded round-robin across N worker
processes, each with its own progress/output file (progress_{i}.json,
tagged_projects_{i}.csv) and its own subset of API keys. Run
`merge_worker_outputs()` (or just concat the CSVs) once all workers finish.

The taxonomy (features, modifiers, descriptions, rules) is defined in
taxonomy.json — edit that file to tune without touching this script.
"""
import signal
import sys
import argparse
import csv
import json
import multiprocessing
import re
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
RETRY_BACKOFF_SECONDS = 5
MIN_SECONDS_BETWEEN_CALLS = 6.5
DEFAULT_TIMEOUT_RETRY_SECONDS = 125
MODEL = "z-ai/glm-5.2"

STATE_DIR = Path("tagging_state")
TAXONOMY_PATH = Path(__file__).parent.parent / "taxonomy/taxonomy.json"

ALL_KEYS = [
    settings.LLM_API_KEY,
    settings.LLM_API_KEY_2,
    settings.LLM_API_KEY_3,
    settings.LLM_API_KEY_4,
]

# These are set per-process in `init_worker_client` / at module import time
# for the non-parallel (--para 1) path.
client = None
KEY_POOL: list[str] = []
_key_index = 0
_last_call_time = 0.0



# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------
_call_durations: list[float] = []


def _record_call_duration(duration: float, worker_tag: str = ""):
    global _call_durations
    _call_durations.append(duration)
    avg = sum(_call_durations) / len(_call_durations)
    prefix = f"[{worker_tag}] " if worker_tag else ""
    print(f"{prefix}⏱  response took {duration:.2f}s "
          f"(avg over {len(_call_durations)} calls: {avg:.2f}s)")


def _save_call_timing(worker_id: int | None):
    suffix = f"_{worker_id}" if worker_id is not None else ""
    path = STATE_DIR / f"call_timing{suffix}.json"
    save_json(path, {
        "count": len(_call_durations),
        "total_seconds": sum(_call_durations),
        "average_seconds": (sum(_call_durations) / len(_call_durations)) if _call_durations else 0,
        "durations": _call_durations,
    })

def get_key_pools(para: int) -> list[list[str]]:
    """
    Splits ALL_KEYS into `para` pools, one per worker.

    - If len(ALL_KEYS) is evenly divisible by `para` (para in 1, 2, 4),
      each worker gets a private contiguous chunk of keys and only
      switches within that chunk on rate limit.
    - Otherwise (e.g. para=3), an even split isn't possible. Each worker
      still starts on a distinct key (round-robin assigned), but its
      switch-on-rate-limit fallback cycles through the FULL key list in
      a fixed linear order, since there's no clean private subset.
    """
    n = len(ALL_KEYS)
    if para <= 0:
        raise ValueError("--para must be >= 1")

    if n % para == 0:
        chunk = n // para
        return [ALL_KEYS[i * chunk:(i + 1) * chunk] for i in range(para)]

    # Uneven split: fall back to "everyone shares the full linear list",
    # just starting from a different offset per worker.
    pools = []
    for i in range(para):
        rotated = ALL_KEYS[i % n:] + ALL_KEYS[:i % n]
        pools.append(rotated)
    return pools


def init_worker_client(key_pool: list[str]):
    """Call once per process before any call_llm() calls."""
    global client, KEY_POOL, _key_index
    KEY_POOL = key_pool
    _key_index = 0
    client = OpenAI(base_url=settings.LLM_BASE_URL, api_key=KEY_POOL[0])


# ---------------------------------------------------------------------------
# Load taxonomy from JSON - single source of truth
# ---------------------------------------------------------------------------
def load_taxonomy(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_derived(tx: dict) -> dict:
    """
    Pre-compute everything the script needs from the raw taxonomy dict:
    flat lists for CSV headers, the prompt string, lookup dicts for
    flatten_tagged. Called once at startup.
    """
    features        = tx["features"]["values"]
    app_types       = tx["application_types"]["values"]
    app_definitions = tx["application_types"].get("definitions", {})
    tech_mods       = tx["technology_modifiers"]["values"]
    numeric_fields  = tx["numeric_modifiers"]["fields"]   # {name: description}
    boolean_fields  = tx["boolean_modifiers"]["fields"]   # {name: description}
    enum_fields     = tx["enum_modifiers"]["fields"]       # {name: {values, description}}
    rules           = tx["prompt_rules"]["rules"]

    csv_columns = (
        ["project_id", "title", "category", "organization", "created_at"]
        + features
        + app_types
        + tech_mods
        + list(numeric_fields.keys())
        + list(boolean_fields.keys())
        + list(enum_fields.keys())
    )

    numeric_prompt_lines = "\n".join(
        f"- {name}: {desc}" for name, desc in numeric_fields.items()
    )
    boolean_prompt_lines = "\n".join(
        f"- {name}: {desc}" for name, desc in boolean_fields.items()
    )
    enum_prompt_lines = "\n".join(
        f"- {name} ({' | '.join(info['values'])}): {info['description']}"
        for name, info in enum_fields.items()
    )

    app_def_lines = "\n".join(
        f'- "{k}": {v}' for k, v in app_definitions.items()
    ) if app_definitions else ""

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
# Helpers
# ---------------------------------------------------------------------------
def load_json(path: Path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data):
    STATE_DIR.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def extract_json(raw_text: str):
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


def _extract_retry_after_seconds(error: Exception) -> float | None:
    body = getattr(error, "body", None)
    if isinstance(body, dict) and "retry_after" in body:
        try:
            return float(body["retry_after"])
        except (TypeError, ValueError):
            pass
    match = re.search(r"['\"]retry_after['\"]\s*:\s*(\d+(?:\.\d+)?)", str(error))
    if match:
        return float(match.group(1))
    return None


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
            (STATE_DIR / f"last_request{suffix}.txt").write_text(
                f"=== SYSTEM ===\n{TAXONOMY['system_prompt']}\n\n=== USER ===\n{user_content}",
                encoding="utf-8",
            )
            (STATE_DIR / f"last_response{suffix}.txt").write_text(result, encoding="utf-8")

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


def write_rows(rows: list[dict], output_path: Path, first_write: bool):
    STATE_DIR.mkdir(exist_ok=True)
    mode = "w" if first_write else "a"
    with open(output_path, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TAXONOMY["csv_columns"])
        if first_write:
            writer.writeheader()
        writer.writerows(rows)


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
# Core tagging logic
# ---------------------------------------------------------------------------
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
# Main orchestration (single worker)
# ---------------------------------------------------------------------------
def run(
    organization_name: str | None,
    limit: int | None,
    key_pool: list[str] | None = None,
    worker_id: int | None = None,
    num_workers: int = 1,
):
    worker_tag = f"w{worker_id}" if worker_id is not None else ""
    suffix = f"_{worker_id}" if worker_id is not None else ""
    progress_path = STATE_DIR / f"progress{suffix}.json"
    output_path = STATE_DIR / f"tagged_projects{suffix}.csv"

    init_worker_client(key_pool or ALL_KEYS)

    try:
        _run_body(organization_name, limit, worker_id, num_workers,
                   worker_tag, progress_path, output_path)
    finally:
        _save_call_timing(worker_id)


def _run_body(organization_name, limit, worker_id, num_workers,
              worker_tag, progress_path, output_path):
    progress = load_json(progress_path, default={
        "last_created_at": None,
        "projects_tagged": 0,
        "batches_completed": 0,
    })

    after_created_at = progress["last_created_at"]
    first_write = after_created_at is None

    prefix = f"[{worker_tag}] " if worker_tag else ""
    if after_created_at:
        print(f"{prefix}↻ Resuming after {after_created_at} ({progress['projects_tagged']} projects already tagged)")

    all_projects = fetch_projects(None, organization_name, None)

    if num_workers > 1:
        shard = [p for i, p in enumerate(all_projects) if i % num_workers == worker_id]
    else:
        shard = all_projects

    if after_created_at:
        shard = [p for p in shard if p["created_at"] > after_created_at]

    if limit:
        shard = shard[:limit]

    total = len(shard)
    print(f"{prefix}📦 {total} projects to tag"
          + (f" (filtered to {organization_name})" if organization_name else ""))

    if total == 0:
        print(f"{prefix}✅ Nothing to do.")
        return

    for batch_start in range(0, total, BATCH_SIZE):
        batch = shard[batch_start: batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"\n{prefix}--- Batch {batch_num}/{total_batches} ({len(batch)} projects) ---")
        for p in batch:
            print(f"{prefix}   {p['title'][:60]}")

        try:
            tagged_by_id = tag_batch(batch, worker_tag=worker_tag)
        except Exception as e:
            print(f"{prefix}❌ Batch {batch_num} failed permanently: {e}")
            print(f"{prefix}   Progress NOT advanced. Re-run to retry this batch.")
            raise

        rows = []
        last_created_at = progress["last_created_at"]
        for p in batch:
            pid = p["project_id"]
            if pid not in tagged_by_id:
                print(f"{prefix}   ⚠️  LLM did not return a result for project {pid} ({p['title'][:40]}) — skipping")
                continue
            row = flatten_tagged(tagged_by_id[pid], p)
            rows.append(row)
            last_created_at = p["created_at"]

        if rows:
            write_rows(rows, output_path, first_write=first_write)
            first_write = False

        progress["last_created_at"] = last_created_at
        progress["projects_tagged"] += len(rows)
        progress["batches_completed"] += 1
        save_json(progress_path, progress)

        print(f"{prefix}   ✅ {len(rows)} rows written (total so far: {progress['projects_tagged']})")

    print(f"\n{prefix}✅ Done. {progress['projects_tagged']} projects tagged → {output_path}")

def _worker_entry(worker_id, num_workers, key_pool, organization_name, limit):
    run(
        organization_name=organization_name,
        limit=limit,
        key_pool=key_pool,
        worker_id=worker_id,
        num_workers=num_workers,
    )

def _print_aggregate_timing(para: int):
    all_durations = []
    for i in range(para):
        path = STATE_DIR / f"call_timing_{i}.json"
        data = load_json(path, default=None)
        if data and data.get("durations"):
            all_durations.extend(data["durations"])

    if not all_durations:
        return

    avg = sum(all_durations) / len(all_durations)
    print(f"\n⏱  Aggregate across {para} workers: {len(all_durations)} calls, "
          f"avg response time {avg:.2f}s, total LLM time {sum(all_durations):.2f}s")

def merge_worker_outputs(para: int, final_path: Path = STATE_DIR / "tagged_projects.csv"):
    writer = None
    rows_written = 0
    with open(final_path, "w", newline="", encoding="utf-8") as out_f:
        for i in range(para):
            part = STATE_DIR / f"tagged_projects_{i}.csv"
            if not part.exists():
                continue
            with open(part, newline="", encoding="utf-8") as in_f:
                reader = csv.DictReader(in_f)
                if reader.fieldnames is None:
                    continue
                if writer is None:
                    writer = csv.DictWriter(out_f, fieldnames=reader.fieldnames)
                    writer.writeheader()
                for row in reader:
                    writer.writerow(row)
                    rows_written += 1
    if writer is not None:
        print(f"✅ Merged {para} worker outputs → {final_path} ({rows_written} rows)")
    else:
        print("⚠️  No worker output files found to merge — nothing written.")

def run_parallel(organization_name: str | None, limit: int | None, para: int):
    key_pools = get_key_pools(para)
    print(f"🧵 Launching {para} parallel workers")
    for i, pool in enumerate(key_pools):
        print(f"   worker {i}: {len(pool)} key(s) starting with ...{pool[0][-4:]}")

    procs = []
    for i in range(para):
        p = multiprocessing.Process(
            target=_worker_entry,
            args=(i, para, key_pools[i], organization_name, limit),
        )
        p.start()
        procs.append(p)

    def _handle_sigint(signum, frame):
        print("\n🛑 Interrupt received — terminating workers, will merge partial output...")
        for p in procs:
            if p.is_alive():
                p.terminate()
        for p in procs:
            p.join()
        merge_worker_outputs(para)
        sys.exit(130)

    old_handler = signal.signal(signal.SIGINT, _handle_sigint)

    try:
        exit_codes = []
        for p in procs:
            p.join()
            exit_codes.append(p.exitcode)

        failed = [i for i, code in enumerate(exit_codes) if code != 0]
        if failed:
            print(f"⚠️  Worker(s) {failed} exited with errors. Merging partial output anyway.")
        else:
            print("✅ All workers finished cleanly.")
    finally:
        signal.signal(signal.SIGINT, old_handler)
        merge_worker_outputs(para)
        _print_aggregate_timing(para)

    if failed:
        raise RuntimeError(f"Worker(s) {failed} failed. Exit codes: {exit_codes}")

def merge_worker_outputs(para: int, final_path: Path = STATE_DIR / "tagged_projects.csv"):
    """Optional helper: concatenate per-worker CSVs into one file."""
    first_write = True
    with open(final_path, "w", newline="", encoding="utf-8") as out_f:
        writer = None
        for i in range(para):
            part = STATE_DIR / f"tagged_projects_{i}.csv"
            if not part.exists():
                continue
            with open(part, newline="", encoding="utf-8") as in_f:
                reader = csv.DictReader(in_f)
                if writer is None:
                    writer = csv.DictWriter(out_f, fieldnames=reader.fieldnames)
                    writer.writeheader()
                for row in reader:
                    writer.writerow(row)
    print(f"✅ Merged {para} worker outputs → {final_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", type=str, default=None,
                        help="Filter to one organization by name (e.g. 'Karlancer')")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max number of projects to tag in this run (applied per-worker when --para > 1)")
    parser.add_argument("--para", type=int, default=1,
                        help="Degree of parallelism — number of worker processes, each with its own API key subset")
    parser.add_argument("--merge", action="store_true",
                        help="Only merge existing per-worker CSVs into tagged_projects.csv (no tagging run)")
    args = parser.parse_args()

    if args.merge:
        merge_worker_outputs(args.para)
        _print_aggregate_timing(args.para)
    elif args.para > 1:
        run_parallel(args.organization, args.limit, args.para)
    else:
        try:
            run(args.organization, args.limit)
        except KeyboardInterrupt:
            print("\n🛑 Interrupted — partial progress/output already saved incrementally.")
            sys.exit(130)