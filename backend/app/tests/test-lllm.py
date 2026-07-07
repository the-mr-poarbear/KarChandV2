from pathlib import Path

from openai import OpenAI
import os
import sys
import json

_USE_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None
_REASONING_COLOR = "\033[90m" if _USE_COLOR else ""
_RESET_COLOR = "\033[0m" if _USE_COLOR else ""

TAXONOMY_PATH = Path(__file__).parent.parent / "taxonomy/taxonomy.json"
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
SYSTEM_PROMPT = build_derived(_raw_taxonomy)


user_content = """
=== USER ===
Tag these 1 projects:

[{"project_id": "aa8681b2-e098-43ca-a650-5d33d8e3cf40", "title": "تکمیل و ویرایش سایت وردپرس", "description": "پلاگین هوشمند مدیریت محصول(قابلیت افزایش یا کاهش قیمت به صورت درصد و ثابت هم داره)\n محصولات نا موجود اخر نمایش داده شوند\n وصل کردن پنل پیامکی(ارسال پیام سفارش برای مدیر و پیامک های تحویل و ثبت سفارش برای مشتری)\n بررسی امنیت و حل مشکلات امنیتی\n بهینه سازی تصاویر در صورت امکان\n حل مشکل بهم ریختگی کادر محصولات در صفحات مختلف\nطراحی و بهینه کردن صفحه ورود\n در صورت", "skills": ["برنامه نویسی", "بررسی امنیت سایت", "طراحی سایت فروشگاهی", "طراحی قالب سایت", "وردپرس", "ووکامرس", "افزونه ووکامرس", " المنتور", "طراحی و برنامه نویسی پلاگین و افزونه"]}]"""


client = OpenAI(
  base_url = "https://integrate.api.nvidia.com/v1",
  api_key = "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"
)



completion = client.chat.completions.create(
    model="z-ai/glm-5.2",
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT["system_prompt"]},
        {"role": "user", "content": user_content},
    ],
    temperature=1,
    top_p=1,
    max_tokens=16384,
    seed=42,
  
  stream=True
)


for chunk in completion:
  if not getattr(chunk, "choices", None):
    continue
  if len(chunk.choices) == 0 or getattr(chunk.choices[0], "delta", None) is None:
    continue
  delta = chunk.choices[0].delta
  if getattr(delta, "content", None) is not None:
    print(delta.content, end="")