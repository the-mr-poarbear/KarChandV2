"""
Tags each project in the database with features and modifiers from the
taxonomy, using an LLM. Outputs a CSV with one row per project and one
column per feature/modifier.

Usage:
    python tag_projects.py
    python tag_projects.py --organization Karlancer
    python tag_projects.py --limit 500

Resumes automatically if interrupted — projects are processed in
created_at order, and progress tracks the last successfully tagged
project's created_at timestamp, so a resume just continues from there.

The taxonomy (features, modifiers, descriptions, rules) is defined in
taxonomy.json — edit that file to tune without touching this script.
"""
import argparse
import csv
import json
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
PROGRESS_PATH = STATE_DIR / "progress.json"
OUTPUT_PATH = STATE_DIR / "tagged_projects.csv"
TAXONOMY_PATH = Path(__file__).parent.parent / "taxonomy/taxonomy.json"

current_api_key = settings.LLM_API_KEY

client = OpenAI(base_url=settings.LLM_BASE_URL, api_key=current_api_key)
_last_call_time = 0.0


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

    # CSV column order: metadata + features + app_types + tech + numeric + boolean + enum
    csv_columns = (
        ["project_id", "title", "category", "organization", "created_at"]
        + features
        + app_types
        + tech_mods
        + list(numeric_fields.keys())
        + list(boolean_fields.keys())
        + list(enum_fields.keys())
    )

    # Build the system prompt dynamically from taxonomy data
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

    # Inject application type definitions as a clarification block
    app_def_lines = "\n".join(
        f'- "{k}": {v}' for k, v in app_definitions.items()
    ) if app_definitions else ""

    # Enum defaults for the output shape example
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


# Load once at startup — any import of this module gets the same derived data
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


def call_llm(user_content: str) -> str:
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < MIN_SECONDS_BETWEEN_CALLS:
        time.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"   🚀  Calling LLM (attempt {attempt}/{MAX_RETRIES})")
        try:
            print("trying to call LLM")
            response = client.chat.completions.create(
                model=MODEL,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": TAXONOMY["system_prompt"]},
                    {"role": "user", "content": user_content},
                ],
            )
            print("LLM call successful")
            _last_call_time = time.monotonic()
            result = response.choices[0].message.content or ""

            STATE_DIR.mkdir(exist_ok=True)
            (STATE_DIR / "last_request.txt").write_text(
                f"=== SYSTEM ===\n{TAXONOMY['system_prompt']}\n\n=== USER ===\n{user_content}",
                encoding="utf-8",
            )
            (STATE_DIR / "last_response.txt").write_text(result, encoding="utf-8")

            return result
        except Exception as e:
            print("exception in call_llm:",e)
            last_error = e
            error_str = str(e)
            retry_after = _extract_retry_after_seconds(e)
            is_rate_limited = "429" in error_str or "rate_limit" in error_str.lower()
            is_gateway_error = any(c in error_str for c in ("502", "503", "504", "524"))

            switched = False

            if client.api_key == settings.LLM_API_KEY:
                print(f"   ⚠️  LLM API key seems rate-limited. Switching to backup key.")
                client.api_key = settings.LLM_API_KEY_2
                switched = True
            elif client.api_key == settings.LLM_API_KEY_2:
                print(f"   ⚠️  LLM API key seems rate-limited. Switching to backup key 3.")
                client.api_key = settings.LLM_API_KEY_3
                switched = True
            elif client.api_key == settings.LLM_API_KEY_3:
                print(f"   ⚠️  LLM API key seems rate-limited. Switching to available key.")
                client.api_key = settings.LLM_API_KEY_4
                switched = True
            else:
                print(f"   ⚠️  All API keys seem rate-limited, fallbacking to original key.")
                client.api_key = settings.LLM_API_KEY

            # if switched:
            #     if retry_after is not None:
            #         wait = retry_after + 5
            #     elif is_rate_limited:
            #         wait = 65
            #     elif is_gateway_error:
            #         wait = DEFAULT_TIMEOUT_RETRY_SECONDS
            #     else:
            #         wait = RETRY_BACKOFF_SECONDS * attempt

            #     print(f"   ⚠️  API call failed (attempt {attempt}/{MAX_RETRIES}): "
            #         f"{error_str[:120]} — retrying in {wait}s")
            #     time.sleep(wait)
    raise RuntimeError(f"API call failed after {MAX_RETRIES} attempts: {last_error}")


def flatten_tagged(tagged: dict, project: dict) -> dict:
    """
    Converts one LLM-returned tagged object into a flat dict matching csv_columns.
    Features, application_type, and technology_modifiers are sparse (only
    non-zero keys returned) — missing ones default to 0.
    Numeric/boolean/enum are always returned in full by the LLM.
    """
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


def write_rows(rows: list[dict], first_write: bool):
    """Append rows to the CSV. Writes the header only on first_write."""
    STATE_DIR.mkdir(exist_ok=True)
    mode = "w" if first_write else "a"
    with open(OUTPUT_PATH, mode, newline="", encoding="utf-8") as f:
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
def tag_batch(batch: list[dict]) -> dict:
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

    raw = call_llm(user_content)

    try:
        tagged_list = extract_json(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse LLM output: {e}\nRaw (first 500 chars):\n{raw[:500]}")

    if not isinstance(tagged_list, list):
        raise RuntimeError(f"Expected JSON array, got {type(tagged_list)}")

    return {item["project_id"]: item for item in tagged_list if "project_id" in item}


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------
def run(organization_name: str | None, limit: int | None):
    progress = load_json(PROGRESS_PATH, default={
        "last_created_at": None,
        "projects_tagged": 0,
        "batches_completed": 0,
    })

    after_created_at = progress["last_created_at"]
    first_write = after_created_at is None

    if after_created_at:
        print(f"↻ Resuming after {after_created_at} ({progress['projects_tagged']} projects already tagged)")

    projects = fetch_projects(after_created_at, organization_name, limit)
    total = len(projects)
    print(f"📦 {total} projects to tag"
          + (f" (filtered to {organization_name})" if organization_name else ""))

    if total == 0:
        print("✅ Nothing to do.")
        return

    for batch_start in range(0, total, BATCH_SIZE):
        batch = projects[batch_start: batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        print(f"\n--- Batch {batch_num}/{total_batches} ({len(batch)} projects) ---")
        for p in batch:
            print(f"   {p['title'][:60]}")

        try:
            tagged_by_id = tag_batch(batch)
        except Exception as e:
            print(f"❌ Batch {batch_num} failed permanently: {e}")
            print("   Progress NOT advanced. Re-run to retry this batch.")
            raise

        rows = []
        last_created_at = progress["last_created_at"]
        for p in batch:
            pid = p["project_id"]
            if pid not in tagged_by_id:
                print(f"   ⚠️  LLM did not return a result for project {pid} ({p['title'][:40]}) — skipping")
                continue
            row = flatten_tagged(tagged_by_id[pid], p)
            rows.append(row)
            last_created_at = p["created_at"]

        if rows:
            write_rows(rows, first_write=first_write)
            first_write = False

        progress["last_created_at"] = last_created_at
        progress["projects_tagged"] += len(rows)
        progress["batches_completed"] += 1
        save_json(PROGRESS_PATH, progress)

        print(f"   ✅ {len(rows)} rows written (total so far: {progress['projects_tagged']})")

    print(f"\n✅ Done. {progress['projects_tagged']} projects tagged → {OUTPUT_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization", type=str, default=None,
                        help="Filter to one organization by name (e.g. 'Karlancer')")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max number of projects to tag in this run")
    args = parser.parse_args()

    run(args.organization, args.limit)
