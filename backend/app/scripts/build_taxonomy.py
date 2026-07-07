"""
Builds a software-capability taxonomy from a sample of freelance projects,
processing them in batches with an LLM.

Usage:
    python build_taxonomy.py

Resumes automatically if interrupted - re-run the same command and it will
pick up from the last completed batch using state saved on disk.
"""
import json
import os
import re
import time
from pathlib import Path

from openai import OpenAI
from app.core.config import settings

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BATCH_SIZE = 20
MERGE_EVERY_N_BATCHES = 5  # run a dedup/merge pass after every N batches
MODEL = "mistral-large"  # via NaraRouter - not a reasoning model, so no reasoning_effort needed
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5
MIN_SECONDS_BETWEEN_CALLS = 6.5  # free tier: 10 req/min -> 6s/req minimum, plus a small margin
DEFAULT_TIMEOUT_RETRY_SECONDS = 125  # a bit more than Cloudflare's 120s proxy read timeout

STATE_DIR = Path("taxonomy_state")
TAXONOMY_PATH = STATE_DIR / "taxonomy.json"
PROGRESS_PATH = STATE_DIR / "progress.json"  # tracks which batch index we're on

client = OpenAI(
    base_url=settings.LLM_BASE_URL,
    api_key=settings.LLM_API_KEY,
)
_last_call_time = 0.0  # used by call_model_with_retry to pace requests under the rate limit

# ---------------------------------------------------------------------------
# Stable instructions, sent as a system message on every call.
# ---------------------------------------------------------------------------
SYSTEM_INSTRUCTIONS = """You are a senior software architect.
We are building a software feature taxonomy.

You will be given:
1. The CURRENT TAXONOMY so far (a JSON array, possibly empty).
2. A batch of freelance project descriptions.

Your job:
1. Read every project.
2. Compare each project's capabilities against the current taxonomy.
3. If a capability already exists in the taxonomy (including under a
   different name/synonym), DO NOTHING for that capability.
4. If you find a genuinely NEW reusable software capability not already
   covered, add it.

Rules:
- Compare SEMANTICALLY, not just by string match.
- Never create a duplicate entry for a capability that already exists
  under a different name - if it's the same capability, skip it.
- Ignore frameworks, programming languages, databases, and cloud providers.
  Only extract software CAPABILITIES (what the software does, not what
  it's built with).

CRITICAL - ABSTRACTION LEVEL:
Capabilities must be reusable across many different industries and domains.
Ask yourself: "Would a developer building a completely different kind of app
ever need this same capability?" If no, it's too specific — skip it.

Too specific (skip these):
- "Warranty Expiry Date Calculator" → domain logic, not a capability
- "Employee Overtime Pay Report" → domain logic
- "Student Grade Submission Form" → domain logic

Correct abstraction (use these instead):
- "Date arithmetic and interval calculations" — needed in scheduling,
  billing, HR, logistics, and many other domains
- "Report generation and export" — needed everywhere
- "Form builder and submission handling" — needed everywhere

IMPORTANT:the features you find should have considerable effect on project's needed salary and expenses 
ignore monor features

If a project mentions calculating expiry dates, extract "Date arithmetic
and interval calculations" — not "Expiry Date Calculator".
If a project mentions payroll math, extract "Financial calculations and
rounding" — not "Payroll Calculator".

The taxonomy should read like a list of building blocks a software team
would reuse, not a list of specific business requirements.

For every NEW major capability you find (at the correct abstraction level),
return an object shaped like:
{
    "feature_name": "",
    "category": "",
}

Return ONLY a JSON array of NEW capability objects (empty array if none are
new). Do not return capabilities that already exist in the current taxonomy.
Do not wrap the JSON in markdown code fences. Return raw JSON only."""
# Merge pass now asks for a SMALL output: which indices should be merged
# into which, not the full taxonomy re-typed out. This keeps generation
# fast (avoids Cloudflare's 120s proxy timeout on large taxonomies) and the
# script applies the merge locally in Python.
MERGE_INSTRUCTIONS = """You are a senior software architect reviewing a
software feature taxonomy for duplicates.

You will be given a numbered list of taxonomy entries (index: feature_name
and aliases only). Some entries may describe the SAME underlying capability
under different names (semantic duplicates), even though they aren't exact
string matches.

Your job: find groups of indices that describe the same capability.

For each group found, return an object:
{
    "keep_index": <the index whose feature_name is clearest/best>,
    "merge_indices": [<other indices in this group, NOT including keep_index>]
}

Only include entries that actually have a duplicate. Do NOT include groups
of size 1. Do NOT re-type feature names, aliases, or examples - indices
only. If there are no duplicates at all, return an empty array.

Return ONLY a JSON array of these merge-group objects. Do not wrap in
markdown code fences. Return raw JSON only."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def batches(items, size):
    for i in range(0, len(items), size):
        yield i // size, items[i : i + size]  # (batch_index, batch_items)


def load_json(path: Path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path: Path, data):
    STATE_DIR.mkdir(exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)  # atomic-ish swap, avoids truncated files on crash mid-write


def remove_exact_duplicates(taxonomy: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for entry in taxonomy:
        key = entry.get("feature_name", "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped


def extract_json(raw_text: str):
    """
    Models sometimes add a preamble before/after a fence, wrap JSON in
    markdown fences, or add trailing commentary despite instructions.
    Strips fences if present, then extracts the outermost JSON array/object
    by finding its matching closing bracket (properly tracking string
    contents, so a brace/bracket character inside a quoted string doesn't
    throw off the count) - so leading or trailing prose around the JSON
    doesn't break parsing.
    """
    text = raw_text.strip()

    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    if start == -1:
        raise json.JSONDecodeError("No JSON array or object found in response", text, 0)

    open_char = text[start]
    close_char = "]" if open_char == "[" else "}"

    depth = 0
    end = -1
    in_string = False
    escape = False
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
        raise json.JSONDecodeError("Could not find matching closing bracket", text, start)

    return json.loads(text[start:end])


def _extract_retry_after_seconds(error: Exception) -> float | None:
    """
    Tries to pull an explicit retry_after value out of the error, if the
    provider/gateway supplied one (NaraRouter's Cloudflare-fronted errors
    do, e.g. {"retryable": true, "retry_after": 120}). Falls back to
    scanning the string form for a "retry_after": N pattern since the SDK
    may surface this as a plain exception message rather than structured
    data depending on how the error was raised.
    """
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


def call_model_with_retry(system_text: str, user_content: str) -> str:
    """
    Calls the API with retry-on-failure. Raises on final failure so the
    caller can decide how to record/skip the batch - we never silently
    swallow a permanently-failed batch.

    Paces calls to stay under the free-tier rate limit (10 req/min) rather
    than relying entirely on reactive retry after a 429. On error, honors
    an explicit retry_after from the provider when present (e.g. Cloudflare
    524 origin timeouts, which report retry_after: 120) instead of always
    using a short generic backoff that just repeats the same timeout.
    """
    global _last_call_time

    elapsed = time.monotonic() - _last_call_time
    if elapsed < MIN_SECONDS_BETWEEN_CALLS:
        time.sleep(MIN_SECONDS_BETWEEN_CALLS - elapsed)

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": system_text},
                    {"role": "user", "content": user_content},
                ],
            )
            _last_call_time = time.monotonic()
            return response.choices[0].message.content or ""
        except Exception as e:
            last_error = e
            error_str = str(e)

            retry_after = _extract_retry_after_seconds(e)
            is_rate_limited = "429" in error_str or "rate_limit" in error_str.lower()
            is_origin_timeout = "524" in error_str or "origin_response_timeout" in error_str

            if retry_after is not None:
                wait = retry_after
            elif is_rate_limited:
                wait = 65
            elif is_origin_timeout:
                wait = DEFAULT_TIMEOUT_RETRY_SECONDS
            else:
                wait = RETRY_BACKOFF_SECONDS * attempt

            print(f"   ⚠️  API call failed (attempt {attempt}/{MAX_RETRIES}): {e} — retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"API call failed after {MAX_RETRIES} attempts: {last_error}")


# ---------------------------------------------------------------------------
# Main batch step: compare a batch of projects against the current taxonomy
# ---------------------------------------------------------------------------
def process_batch(taxonomy: list[dict], batch: list[dict]) -> list[dict]:
    user_content = (
        f"CURRENT TAXONOMY ({len(taxonomy)} entries):\n"
        f"{json.dumps(taxonomy, ensure_ascii=False)}\n\n"
        f"PROJECTS ({len(batch)} items):\n"
        f"{json.dumps(batch, ensure_ascii=False)}"
    )

    raw_text = call_model_with_retry(SYSTEM_INSTRUCTIONS, user_content)

    try:
        new_features = extract_json(raw_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse model output as JSON: {e}\nRaw output:\n{raw_text[:500]}")

    if not isinstance(new_features, list):
        raise RuntimeError(f"Expected a JSON array of new features, got: {type(new_features)}")

    return new_features


# ---------------------------------------------------------------------------
# Periodic merge pass: ask the model which entries are duplicates (by index
# only - small output), then apply the merge locally in Python.
# ---------------------------------------------------------------------------
def _apply_merge_groups(taxonomy: list[dict], merge_groups: list[dict]) -> list[dict]:
    """
    merge_groups: [{"keep_index": int, "merge_indices": [int, ...]}, ...]
    Combines aliases/examples from merged-away entries into the kept entry,
    then drops the merged-away entries. Runs entirely in Python - no model
    call needed to reconstruct the array.
    """
    to_drop = set()
    for group in merge_groups:
        keep_idx = group.get("keep_index")
        merge_idxs = group.get("merge_indices", [])

        if keep_idx is None or keep_idx < 0 or keep_idx >= len(taxonomy):
            print(f"   ⚠️  Skipping merge group with invalid keep_index: {group}")
            continue

        keep_entry = taxonomy[keep_idx]
        for idx in merge_idxs:
            if idx == keep_idx or idx < 0 or idx >= len(taxonomy) or idx in to_drop:
                continue
            dropped_entry = taxonomy[idx]

            # Fold the dropped entry's own name in as an alias too, so the
            # merge doesn't lose the fact that this name was ever used.
            keep_entry.setdefault("aliases", [])
            for alias in [dropped_entry.get("feature_name", "")] + dropped_entry.get("aliases", []):
                if alias and alias not in keep_entry["aliases"] and alias != keep_entry.get("feature_name"):
                    keep_entry["aliases"].append(alias)

            keep_entry.setdefault("implicit_examples", [])
            keep_entry["implicit_examples"].extend(
                ex for ex in dropped_entry.get("implicit_examples", [])
                if ex not in keep_entry["implicit_examples"]
            )
            keep_entry.setdefault("explicit_examples", [])
            keep_entry["explicit_examples"].extend(
                ex for ex in dropped_entry.get("explicit_examples", [])
                if ex not in keep_entry["explicit_examples"]
            )

            to_drop.add(idx)

    return [entry for i, entry in enumerate(taxonomy) if i not in to_drop]


def run_merge_pass(taxonomy: list[dict]) -> list[dict]:
    if len(taxonomy) < 2:
        return taxonomy

    print(f"🧹 Running merge pass on {len(taxonomy)} entries...")

    # Small, indexed summary - NOT the full entries with examples - keeps
    # both the input and the requested output small and fast to generate,
    # which matters once the taxonomy grows past ~50 entries (this is what
    # was hitting Cloudflare's 120s origin timeout before).
    indexed_summary = [
        {"index": i, "feature_name": e.get("feature_name", ""), "aliases": e.get("aliases", [])}
        for i, e in enumerate(taxonomy)
    ]
    user_content = json.dumps(indexed_summary, ensure_ascii=False)

    raw_text = call_model_with_retry(MERGE_INSTRUCTIONS, user_content)

    try:
        merge_groups = extract_json(raw_text)
    except json.JSONDecodeError as e:
        print(f"   ⚠️  Merge pass returned unparseable JSON, skipping this round: {e}")
        return taxonomy

    if not isinstance(merge_groups, list):
        print("   ⚠️  Merge pass returned something unexpected, skipping this round.")
        return taxonomy

    if not merge_groups:
        print("   → no duplicates found this round")
        return taxonomy

    merged = _apply_merge_groups(taxonomy, merge_groups)
    print(f"   → taxonomy size after merge: {len(merged)} (was {len(taxonomy)}, "
          f"{len(merge_groups)} merge group(s) applied)")
    return merged


# ---------------------------------------------------------------------------
# Orchestration with resume support
# ---------------------------------------------------------------------------
def build_taxonomy(sample: list[dict]):
    taxonomy = load_json(TAXONOMY_PATH, default=[])
    progress = load_json(PROGRESS_PATH, default={
        "next_batch_index": 0,
        "pending_merge": False,  # True if we saved new features but crashed before/during merge
    })
    start_index = progress["next_batch_index"]
    pending_merge = progress.get("pending_merge", False)

    all_batches = list(batches(sample, BATCH_SIZE))
    total_batches = len(all_batches)

    if start_index > 0:
        print(f"↻ Resuming from batch {start_index}/{total_batches} "
              f"(taxonomy already has {len(taxonomy)} entries)")

    # If the last run crashed during/before a merge pass, finish it now
    # before processing any new batches — the taxonomy was already saved
    # with the new features from that batch, so we just need the merge.
    if pending_merge:
        print(f"↻ Completing interrupted merge pass from previous run...")
        try:
            taxonomy = run_merge_pass(taxonomy)
            save_json(TAXONOMY_PATH, taxonomy)
        except Exception as e:
            print(f"   ⚠️  Merge pass failed again, continuing without merging: {e}")
        progress["pending_merge"] = False
        save_json(PROGRESS_PATH, progress)

    for batch_index, batch in all_batches:
        if batch_index < start_index:
            continue  # already processed in a prior run

        print(f"\n--- Batch {batch_index + 1}/{total_batches} ({len(batch)} projects) ---")

        try:
            new_features = process_batch(taxonomy, batch)
        except Exception as e:
            print(f"❌ Batch {batch_index + 1} failed permanently: {e}")
            print("   Progress NOT advanced for this batch. Re-run the script to retry it.")
            save_json(TAXONOMY_PATH, taxonomy)  # save whatever we have so far
            raise

        if new_features:
            print(f"   + {len(new_features)} new feature(s): "
                  f"{[f.get('feature_name') for f in new_features]}")
        else:
            print("   + 0 new features (all capabilities already covered)")

        taxonomy.extend(new_features)
        taxonomy = remove_exact_duplicates(taxonomy)

        # Save the new features and advance batch progress BEFORE attempting
        # the merge pass. This way, if the merge crashes:
        # - the new features from this batch are safely on disk
        # - progress is advanced so we don't re-process this batch
        # - pending_merge=True tells the next run to finish the merge first
        is_merge_point = (batch_index + 1) % MERGE_EVERY_N_BATCHES == 0
        save_json(TAXONOMY_PATH, taxonomy)
        progress["next_batch_index"] = batch_index + 1
        progress["pending_merge"] = is_merge_point
        save_json(PROGRESS_PATH, progress)

        if is_merge_point:
            try:
                taxonomy = run_merge_pass(taxonomy)
                save_json(TAXONOMY_PATH, taxonomy)
            except Exception as e:
                print(f"   ⚠️  Merge pass failed, will retry on next resume: {e}")
                # Leave pending_merge=True in progress so next run retries it.
                # The batch itself is already committed, so no work is lost.
                continue
            # Merge succeeded — clear the pending flag
            progress["pending_merge"] = False
            save_json(PROGRESS_PATH, progress)

    print(f"\n✅ Done. Final taxonomy: {len(taxonomy)} entries across "
          f"{total_batches} batches.")
    return taxonomy


if __name__ == "__main__":
    # Replace this with however you actually load your 1000-sample dataset.
    # Expected shape: list of dicts (or strings) describing each project.
    with open("project_sample.json", "r", encoding="utf-8") as f:
        sample = json.load(f)

    build_taxonomy(sample)