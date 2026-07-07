"""
Extracts a sample of projects (with their associated skills) from the
database and writes them to project_sample.json, in the shape expected by
build_taxonomy.py.

Usage:
    python extract_sample.py --limit 1000
    python extract_sample.py --limit 1000 --organization karlancer
    python extract_sample.py --limit 1000 --random
"""
import argparse
import json
from pathlib import Path

from app.core.database import SessionLocal
from app.models.project import Project
from app.models.organization import Organization

OUTPUT_PATH = Path("project_sample.json")


def extract_sample(limit: int, organization_name: str | None, random_order: bool) -> list[dict]:
    db = SessionLocal()
    try:
        from app.models.category import Category

        query = db.query(Project)

        if organization_name:
            query = (
                query.join(Category, Project.category_id == Category.id)
                .join(Organization, Category.organization_id == Organization.id)
                .filter(Organization.name == organization_name)
            )

        if random_order:
            query = query.order_by(func_random())
        else:
            query = query.order_by(Project.created_at.desc())

        projects = query.limit(limit).all()

        sample = []
        for p in projects:
            # Description is what actually carries signal for capability
            # extraction - title alone is too thin, and we deliberately
            # don't include budget/dates/ids, since none of that is
            # relevant to "what software capabilities does this project
            # need" and would just be wasted tokens in every LLM call.
            sample.append({
                "title": p.title,
                "description": p.description,
                "skills": [s.name for s in p.skills],
            })

        return sample
    finally:
        db.close()


def func_random():
    """Postgres RANDOM() for ORDER BY - kept as a tiny helper so the import
    of sqlalchemy.func stays localized to where it's actually used."""
    from sqlalchemy import func
    return func.random()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1000, help="Number of projects to extract")
    parser.add_argument(
        "--organization", type=str, default=None,
        help="Filter to one organization by name (e.g. 'Karlancer' or 'Ponisha'). Omit for all."
    )
    parser.add_argument(
        "--random", action="store_true",
        help="Random sample instead of most-recent-first. Slower on large tables (no index on RANDOM())."
    )
    args = parser.parse_args()

    sample = extract_sample(args.limit, args.organization, args.random)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)

    print(f"✅ Wrote {len(sample)} projects to {OUTPUT_PATH}")
    if sample:
        with_skills = sum(1 for p in sample if p["skills"])
        with_description = sum(1 for p in sample if p["description"])
        print(f"   {with_skills}/{len(sample)} have at least one skill")
        print(f"   {with_description}/{len(sample)} have a non-empty description")