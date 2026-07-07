"""
Joins the tagged projects CSV (from tag_projects.py) with raw project
details from the database, producing a single ML-ready dataset.

Also normalizes IRR prices to USD using the daily `usd` exchange-rate
table, so a 15-year price history isn't distorted by IRR's instability.
Since the `usd` table has gaps (missing dates), we attach the *nearest*
available rate to each project using pandas' merge_asof instead of
issuing a per-row "closest date" SQL query.

Output: ml_dataset/full_dataset.csv
        ml_dataset/training_set.csv  (rows with final_budget > 0)

Usage:
    python build_dataset.py
    python build_dataset.py --tagged tagging_state/tagged_projects.csv
"""
import argparse
import json
from pathlib import Path

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

from app.core.config import settings

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OUTPUT_DIR        = Path("ml_dataset")
TAGGED_CSV        = Path("tagging_state/tagged_projects.csv")
FULL_OUTPUT       = OUTPUT_DIR / "full_dataset.csv"
TRAINING_OUTPUT   = OUTPUT_DIR / "training_set.csv"
REPORT_OUTPUT     = OUTPUT_DIR / "dataset_report.txt"

# ---------------------------------------------------------------------------
# Fetch project details from DB
# ---------------------------------------------------------------------------
def fetch_project_details(engine) -> pd.DataFrame:
    query = text("""
        SELECT
            p.id::text                          AS project_id,
            p.title,
            p.description,
            p.outer_link,
            p.duration,
            p.budget_min,
            p.budget_max,
            p.final_budget,
            p.scraped_date_created,
            p.created_at,
            p.updated_at,
            cat.name                            AS category,
            cat.scraped_id                      AS category_scraped_id,
            org.name                            AS organization,

            -- Skills: aggregated as a pipe-separated string and a count
            COALESCE(
                STRING_AGG(sk.name, ' | ' ORDER BY sk.name), ''
            )                                   AS skills,
            COUNT(sk.id)                        AS skill_count

        FROM projects p
        LEFT JOIN categories   cat ON p.category_id   = cat.id
        LEFT JOIN organizations org ON cat.organization_id = org.id
        LEFT JOIN projects_skills ps  ON p.id = ps.project_id
        LEFT JOIN skills         sk  ON ps.skill_id = sk.id
        GROUP BY
            p.id, p.title, p.description, p.outer_link, p.duration,
            p.budget_min, p.budget_max, p.final_budget,
            p.scraped_date_created, p.created_at, p.updated_at,
            cat.name, cat.scraped_id, org.name
        ORDER BY p.created_at ASC
    """)

    with engine.connect() as conn:
        result = conn.execute(query)
        df = pd.DataFrame(result.fetchall(), columns=result.keys())

    # Postgres NUMERIC columns come back as Python Decimal objects (object
    # dtype), which breaks numpy ufuncs like log1p downstream. Coerce to
    # float64 here so all later arithmetic is vectorized properly.
    for col in ['budget_min', 'budget_max', 'final_budget']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype(float)

    print(f"  Fetched {len(df):,} project rows from DB")
    return df


# ---------------------------------------------------------------------------
# Fetch USD exchange rates
# ---------------------------------------------------------------------------
def fetch_usd_rates(engine) -> pd.DataFrame:
    """
    Pulls the full usd table. The reference rate is the average of
    open/high/low/close for the day. Returned sorted + de-duplicated
    by date, ready for merge_asof.
    """
    query = text("""
        SELECT date, open, high, low, close
        FROM usd
        ORDER BY date ASC
    """)
    with engine.connect() as conn:
        result = conn.execute(query)
        usd = pd.DataFrame(result.fetchall(), columns=result.keys())

    usd['date'] = pd.to_datetime(usd['date']).astype('datetime64[ns]')
    usd = usd.drop_duplicates(subset='date').sort_values('date').reset_index(drop=True)

    # Same Decimal-from-Postgres issue as budgets — coerce to float64.
    for col in ['open', 'high', 'low', 'close']:
        usd[col] = pd.to_numeric(usd[col], errors='coerce').astype(float)

    usd['avg_rate'] = usd[['open', 'high', 'low', 'close']].mean(axis=1)

    print(f"  Fetched {len(usd):,} USD rate rows "
          f"({usd['date'].min().date()} → {usd['date'].max().date()})")
    return usd


def attach_usd_rates(merged: pd.DataFrame, usd: pd.DataFrame) -> pd.DataFrame:
    """
    Attaches the nearest available USD rate (average of open/high/low/close)
    to each project, based on scraped_date_created. Projects with no
    scraped_date_created are left null — we do NOT fall back to created_at,
    since that's when it was scraped/inserted, not when the project was
    actually posted, and would misattribute the exchange rate.

    Uses merge_asof(direction='nearest') instead of a per-row SQL
    "closest date" query, since usd has gaps and this is a single
    vectorized operation regardless of how many projects we have.
    """
    merged = merged.copy()

    merged['_rate_ref_date'] = pd.to_datetime(
        merged['scraped_date_created'], errors='coerce'
    ).dt.normalize().astype('datetime64[ns]')

    has_ref_date = merged['_rate_ref_date'].notna()

    # merge_asof requires the "on" columns sorted ascending, and can't
    # handle NaT keys — so split those rows out and rejoin after.
    left = merged.loc[has_ref_date].reset_index().sort_values('_rate_ref_date')
    without_date = merged.loc[~has_ref_date].reset_index()

    usd_for_merge = usd[['date', 'avg_rate']].copy()
    usd_for_merge['date'] = usd_for_merge['date'].astype('datetime64[ns]')

    joined_with_date = pd.merge_asof(
        left,
        usd_for_merge,
        left_on='_rate_ref_date',
        right_on='date',
        direction='nearest',
    )

    without_date['date'] = pd.NaT
    without_date['avg_rate'] = np.nan

    joined = pd.concat([joined_with_date, without_date], ignore_index=True)
    joined = joined.rename(columns={'avg_rate': 'usd_rate', 'date': 'usd_rate_date'})
    joined = joined.sort_values('index').set_index('index').drop(
        columns=['_rate_ref_date'], errors='ignore'
    )
    joined.index.name = None

    n_no_ref_date = (~has_ref_date).sum()
    if n_no_ref_date > 0:
        print(f"  ⚠️  {n_no_ref_date:,} projects have no scraped_date_created "
              f"— usd_rate left null (no fallback to created_at)")

    return joined


# ---------------------------------------------------------------------------
# Main join + enrichment
# ---------------------------------------------------------------------------
def build_dataset(tagged_csv: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    print("Loading tagged CSV...")
    tagged = pd.read_csv(tagged_csv)
    print(f"  Tagged rows: {len(tagged):,}")

    print("Fetching project details from database...")
    engine = create_engine(settings.DATABASE_URL)
    details = fetch_project_details(engine)

    print("Fetching USD exchange rates from database...")
    usd = fetch_usd_rates(engine)

    # Drop columns already in tagged CSV that we'll get from DB (avoid conflicts)
    details_to_merge = details.drop(
        columns=[c for c in ['title', 'category', 'organization', 'created_at']
                 if c in details.columns],
        errors='ignore'
    )

    print("Joining...")
    merged = tagged.merge(details_to_merge, on='project_id', how='left')

    missing_details = merged['description'].isna().sum()
    if missing_details > 0:
        print(f"  ⚠️  {missing_details:,} tagged rows have no matching DB project "
              f"(project may have been deleted — they are kept with null detail columns)")

    print("Attaching nearest USD exchange rate to each project...")
    merged = attach_usd_rates(merged, usd)

    # ── Derived / enrichment columns ──────────────────────────────────────

    # Belt-and-suspenders: make sure these are float64 before any log1p /
    # division, regardless of what dtype they arrived as through the merges.
    for col in ['final_budget', 'budget_min', 'budget_max', 'usd_rate']:
        merged[col] = pd.to_numeric(merged[col], errors='coerce').astype(float)

    # Log budget (IRR — kept for reference, not recommended as the
    # regression target given IRR's 15-year instability)
    merged['log_final_budget'] = np.log1p(merged['final_budget'])
    merged['log_budget_min']   = np.log1p(merged['budget_min'])
    merged['log_budget_max']   = np.log1p(merged['budget_max'])

    # ── USD-normalized prices (the recommended regression targets) ───────
    # usd_rate is IRR per 1 USD (close price), so USD amount = IRR / rate.
    merged['final_budget_usd'] = merged['final_budget'] / merged['usd_rate']
    merged['budget_min_usd']   = merged['budget_min']   / merged['usd_rate']
    merged['budget_max_usd']   = merged['budget_max']   / merged['usd_rate']

    merged['log_final_budget_usd'] = np.log1p(merged['final_budget_usd'])
    merged['log_budget_min_usd']   = np.log1p(merged['budget_min_usd'])
    merged['log_budget_max_usd']   = np.log1p(merged['budget_max_usd'])

    # Budget range (how wide is the client's expectation?) — in USD
    merged['budget_range_usd'] = merged['budget_max_usd'] - merged['budget_min_usd']
    merged['budget_range_ratio'] = np.where(
        merged['budget_min'] > 0,
        merged['budget_max'] / merged['budget_min'],
        np.nan
    )

    # Has final budget flag (require a usable USD conversion too)
    merged['has_final_budget'] = (
        merged['final_budget'].notna() & (merged['final_budget'] > 0)
        & merged['usd_rate'].notna()
    )

    # Description length (proxy for project detail / client effort)
    merged['description_length'] = merged['description'].fillna('').str.len()
    merged['description_word_count'] = merged['description'].fillna('').str.split().str.len()

    # Duration parsing (Ponisha stores "N days" as string)
    merged['duration_days'] = (
        merged['duration']
        .str.extract(r'(\d+(?:\.\d+)?)', expand=False)
        .astype(float)
    )

    # Feature vector stats
    feature_cols = [c for c in tagged.columns if c not in
                    ['project_id','title','category','organization','created_at']]

    # We'll identify feature cols as those before the first application_type
    # by checking if they're numeric and in the expected range 0-5
    app_types = ['website','web_application','mobile_application','desktop_application',
                 'api_backend','ecommerce','marketplace','cms','crm','erp','lms',
                 'booking_system','social_network','media_platform','financial_system',
                 'healthcare_system','real_estate_platform','automation_system',
                 'iot_system','ai_application','browser_extension','other']
    pure_features = [c for c in feature_cols if c not in app_types
                     and c not in ['WordPress','WooCommerce','Joomla','Custom Development']
                     and c not in ['external_api_count','estimated_screens','estimated_entities']
                     and c not in ['real_time','mobile','web','offline','legacy_system',
                                   'existing_codebase','requires_refactoring',
                                   'requires_reverse_engineering','cross_platform']
                     and c not in ['project_type','ai_level','business_logic','data_volume',
                                   'integration_complexity','ui_complexity','algorithmic_complexity']]

    if pure_features:
        merged['feature_score_sum']   = merged[pure_features].sum(axis=1)
        merged['feature_count']       = (merged[pure_features] >= 1).sum(axis=1)
        merged['feature_score_mean']  = merged[pure_features].mean(axis=1)
        merged['feature_score_max']   = merged[pure_features].max(axis=1)

    # ── Column ordering ────────────────────────────────────────────────────
    # Put the most important/interpretable columns first
    front_cols = [
        'project_id', 'title', 'category', 'organization',
        'created_at', 'scraped_date_created',
        'usd_rate', 'usd_rate_date',
        'final_budget', 'log_final_budget',
        'final_budget_usd', 'log_final_budget_usd',
        'budget_min', 'budget_max', 'log_budget_min', 'log_budget_max',
        'budget_min_usd', 'budget_max_usd',
        'log_budget_min_usd', 'log_budget_max_usd',
        'budget_range_usd', 'budget_range_ratio',
        'has_final_budget',
        'duration', 'duration_days',
        'description_length', 'description_word_count', 'skill_count',
        'skills', 'description', 'outer_link',
    ]
    front_cols = [c for c in front_cols if c in merged.columns]
    remaining = [c for c in merged.columns if c not in front_cols]
    merged = merged[front_cols + remaining]

    # ── Split into full + training subsets ────────────────────────────────
    full_df = merged.copy()
    training_df = merged[merged['has_final_budget'] == True].copy()

    return full_df, training_df


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def write_report(full_df: pd.DataFrame, training_df: pd.DataFrame, path: Path):
    lines = []
    lines.append("=" * 60)
    lines.append("ML DATASET BUILD REPORT")
    lines.append("=" * 60)
    lines.append(f"Total rows (full dataset)  : {len(full_df):,}")
    lines.append(f"Training rows (has budget) : {len(training_df):,} "
                 f"({len(training_df)/len(full_df):.1%})")
    lines.append(f"Columns                    : {full_df.shape[1]}")
    lines.append("")

    lines.append("--- USD rate coverage ---")
    n_with_rate = full_df['usd_rate'].notna().sum()
    lines.append(f"Projects with a usable USD rate: "
                 f"{n_with_rate:,} ({n_with_rate/len(full_df):.1%})")
    n_no_ref = full_df['scraped_date_created'].isna().sum() if 'scraped_date_created' in full_df.columns else 0
    if n_no_ref > 0:
        lines.append(f"  (of the missing, {n_no_ref:,} have no scraped_date_created at all)")
    lines.append("")

    lines.append("--- Budget stats (training set, USD) ---")
    if len(training_df) > 0:
        for col in ['final_budget_usd', 'budget_min_usd', 'budget_max_usd']:
            if col in training_df.columns:
                s = training_df[col].describe()
                lines.append(f"{col}:")
                lines.append(f"  median={s['50%']:,.2f}  mean={s['mean']:,.2f}  "
                             f"min={s['min']:,.2f}  max={s['max']:,.2f}")

    lines.append("")
    lines.append("--- Organizations ---")
    for org, count in full_df['organization'].value_counts().items():
        lines.append(f"  {org}: {count:,}")

    lines.append("")
    lines.append("--- Missing value summary ---")
    miss = full_df.isnull().sum()
    miss = miss[miss > 0].sort_values(ascending=False)
    for col, n in miss.items():
        lines.append(f"  {col}: {n:,} ({n/len(full_df):.1%})")

    lines.append("")
    lines.append("--- Recommended ML targets ---")
    lines.append("  Primary: log_final_budget_usd (regression, use training_set.csv)")
    lines.append("           IRR targets are NOT recommended given 15 years of")
    lines.append("           currency instability — use the *_usd columns instead.")
    lines.append("  Alt: has_final_budget (classification — did project complete?)")
    lines.append("")
    lines.append("--- Recommended encoding ---")
    lines.append("  Feature scores (0-5): keep as-is or binarize at >=3")
    lines.append("  Enum cols: one-hot encode before training")
    lines.append("  skills: TF-IDF or skill_count as a numeric proxy")
    lines.append("  description: TF-IDF or embedding if using NLP features")

    report = "\n".join(lines)
    path.write_text(report, encoding='utf-8')
    print("\n" + report)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tagged", type=Path, default=TAGGED_CSV,
                        help="Path to the tagged projects CSV")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)

    full_df, training_df = build_dataset(args.tagged)

    print(f"\nSaving full dataset ({len(full_df):,} rows)...")
    full_df.to_csv(FULL_OUTPUT, index=False, encoding='utf-8')
    print(f"  → {FULL_OUTPUT}")

    print(f"Saving training set ({len(training_df):,} rows)...")
    training_df.to_csv(TRAINING_OUTPUT, index=False, encoding='utf-8')
    print(f"  → {TRAINING_OUTPUT}")

    write_report(full_df, training_df, REPORT_OUTPUT)
    print(f"\nReport saved → {REPORT_OUTPUT}")