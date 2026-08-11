"""
Extracts suggested taxonomy tags for a single project description using an LLM.

Given the raw text of one project, calls the LLM once and returns:
{
  "reasoning": str,
  "suggested_settings": {
    "features": list[str],
    "boolean_modifiers": list[str],
    "application_types": list[str],
    "technology_modifiers": list[str],
    "enum_modifiers": dict[str, str],
    "numeric_modifiers": dict[str, float | int],
  }
}

The taxonomy (features, modifiers, descriptions, rules) is defined in
taxonomy.json — edit that file to tune without touching this script.
"""
import json
import re
import time
from pathlib import Path

from fastapi import APIRouter
from openai import OpenAI

from app.core.config import settings

from app.services.Frontend.price_estimator import estimate_price

router = APIRouter()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MAX_RETRIES = 3
MODEL = "z-ai/glm-5.2"
TAXONOMY_PATH = Path(__file__).parent.parent.parent / "taxonomy/taxonomy.json"

client = OpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY)


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

    numeric_prompt_lines = "\n".join(f"- {name}: {desc}" for name, desc in numeric_fields.items())
    boolean_prompt_lines = "\n".join(f"- {name}: {desc}" for name, desc in boolean_fields.items())
    enum_prompt_lines = "\n".join(
        f"- {name} ({' | '.join(info['values'])}): {info['description']}"
        for name, info in enum_fields.items()
    )
    app_def_lines = "\n".join(f'- "{k}": {v}' for k, v in app_definitions.items()) if app_definitions else ""

    enum_defaults = {name: info["values"][0] for name, info in enum_fields.items()}
    numeric_defaults = {name: 0 for name in numeric_fields}

    system_prompt = f"""You are a senior software architect analyzing a single freelance project description.

Read the project text below and decide which taxonomy tags apply to it.

FEATURES (include a feature's exact name ONLY if it is clearly present/required in this
project — do not include weak, implied-only, or absent features):
{json.dumps(features, ensure_ascii=False)}

APPLICATION TYPE (include a type's exact name ONLY if the project belongs to it; multiple allowed):
{json.dumps(app_types, ensure_ascii=False)}
{("Clarifications:\n" + app_def_lines) if app_def_lines else ""}

TECHNOLOGY MODIFIERS (include a technology's exact name ONLY if the project explicitly uses it):
{json.dumps(tech_mods, ensure_ascii=False)}

BOOLEAN MODIFIERS (include the modifier's exact name ONLY if it is true for this project):
{boolean_prompt_lines}

NUMERIC MODIFIERS (always return every key below with your best estimate; use 0 only if truly
none detectable — always estimate rather than omit):
{numeric_prompt_lines}

ENUM MODIFIERS (always return every key below, picking exactly one value from its allowed list):
{enum_prompt_lines}

Rules:
{"".join(f"- {r}{chr(10)}" for r in rules)}

Return ONLY a single JSON object — no markdown fences, no preamble, no trailing text — in this
EXACT shape:
{{
  "reasoning": "<a short paragraph explaining why you chose these tags in persian language>",
  "suggested_settings": {{
    "features": ["<feature_name>", ...],
    "boolean_modifiers": ["<modifier_name>", ...],
    "application_types": ["<type_name>", ...],
    "technology_modifiers": ["<tech_name>", ...],
    "enum_modifiers": {json.dumps(enum_defaults)},
    "numeric_modifiers": {json.dumps(numeric_defaults)}
  }}
}}

NOTE: the reasoning should be in persian language

"""

    return {
        "features": features,
        "app_types": app_types,
        "tech_mods": tech_mods,
        "numeric_fields": list(numeric_fields.keys()),
        "boolean_fields": list(boolean_fields.keys()),
        "enum_fields": list(enum_fields.keys()),
        "enum_allowed": {name: info["values"] for name, info in enum_fields.items()},
        "system_prompt": system_prompt,
    }


_raw_taxonomy = load_taxonomy(TAXONOMY_PATH)
TAXONOMY = build_derived(_raw_taxonomy)


# ---------------------------------------------------------------------------
# Robust JSON extraction (handles fences / preamble / trailing prose)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# LLM call (single call, simple retry — no batching/multiprocessing needed
# for a single project)
# ---------------------------------------------------------------------------
def call_llm(project_text: str) -> str:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"🚀  Calling LLM (attempt {attempt}/{MAX_RETRIES})")
        try:
            response = client.chat.completions.create(
                model=MODEL,
                max_tokens=2048,
                messages=[
                    {"role": "system", "content": TAXONOMY["system_prompt"]},
                    {"role": "user", "content": f"Project description:\n\n{project_text}"},
                ],
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            last_error = e
            print(f"⚠️  attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 * attempt)

    raise RuntimeError(f"API call failed after {MAX_RETRIES} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Normalize whatever the LLM returned against the taxonomy so the response
# to the client is always well-formed (unknown names dropped, missing
# enum/numeric keys filled in, dupes removed).
# ---------------------------------------------------------------------------
def normalize_result(parsed: dict) -> dict:
    suggested = parsed.get("suggested_settings", {}) or {}

    def clean_list(values, allowed: list[str]) -> list[str]:
        allowed_set = set(allowed)
        out, seen = [], set()
        for v in values or []:
            if v in allowed_set and v not in seen:
                out.append(v)
                seen.add(v)
        return out

    features = clean_list(suggested.get("features"), TAXONOMY["features"])
    boolean_modifiers = clean_list(suggested.get("boolean_modifiers"), TAXONOMY["boolean_fields"])
    application_types = clean_list(suggested.get("application_types"), TAXONOMY["app_types"])
    technology_modifiers = clean_list(suggested.get("technology_modifiers"), TAXONOMY["tech_mods"])

    numeric_in = suggested.get("numeric_modifiers", {}) or {}
    numeric_modifiers = {}
    for k in TAXONOMY["numeric_fields"]:
        v = numeric_in.get(k, 0)
        try:
            v = float(v)
            if v == int(v):
                v = int(v)
        except (TypeError, ValueError):
            v = 0
        numeric_modifiers[k] = v

    enum_in = suggested.get("enum_modifiers", {}) or {}
    enum_modifiers = {}
    for k in TAXONOMY["enum_fields"]:
        allowed = TAXONOMY["enum_allowed"].get(k, [])
        val = enum_in.get(k)
        if not allowed or val not in allowed:
            val = allowed[0] if allowed else val
        enum_modifiers[k] = val

    return {
        "reasoning": str(parsed.get("reasoning", "")).strip(),
        "suggested_settings": {
            "features": features,
            "boolean_modifiers": boolean_modifiers,
            "application_types": application_types,
            "technology_modifiers": technology_modifiers,
            "enum_modifiers": enum_modifiers,
            "numeric_modifiers": numeric_modifiers,
        },
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def Extract_Taxonomy(query: str) -> dict:
    """
    Takes the raw text of a single project and returns a dict matching:

      reasoning: string;
      suggested_settings: {
        features: string[];
        boolean_modifiers: string[];
        application_types: string[];
        technology_modifiers: string[];
        enum_modifiers?: Record<string, string>;
        numeric_modifiers?: Record<string, number>;
      };
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")

    raw = call_llm(query)

    try:
        parsed = extract_json(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse LLM output: {e}\nRaw (first 500 chars):\n{raw[:500]}")

    if not isinstance(parsed, dict):
        raise RuntimeError(f"Expected a JSON object, got {type(parsed)}")

    result = normalize_result(parsed)
    result["estimated_price"] = estimate_price(result["suggested_settings"])
    return result

