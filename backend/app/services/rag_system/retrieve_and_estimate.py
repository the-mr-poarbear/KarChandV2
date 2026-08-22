"""
Given a free-text user query describing a project, retrieves the most
similar past projects from Qdrant and builds a prompt asking an LLM to
estimate a price for the new project based on those comparables.

Usage:
    python retrieve_and_estimate.py "طراحی وبسایت فروشگاهی با React و درگاه پرداخت"
"""
import sys
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer

from app.core.config import settings
from app.services.rag_system.common import embed_text

import json
from typing import TypedDict

from openai import OpenAI

from sqlalchemy.orm import Session

from app.services.Frontend.project_matcher import _num , SimilarProject , _to_similar_project_result 
from app.services.usd import get_current_usd_rate

class EstimateResult(TypedDict):
    reasoning: str
    estimated_price: float

TOP_K = 10


def get_client() -> QdrantClient:
    if settings.QDRANT_URL:
        return QdrantClient(url=settings.QDRANT_URL)
\


def search(query: str, model: SentenceTransformer, client: QdrantClient, top_k: int = None):
    top_k = top_k or TOP_K
    q_text = embed_text(model, query, is_query=True)
    q_emb = model.encode(q_text, normalize_embeddings=True).tolist()
    result = client.query_points(
        collection_name=settings.QDRANT_COLLECTION,
        query=q_emb,
        limit=top_k,
    )
    return result.points


def format_comparable(hit) -> str:
    p = hit.payload

    price_line = "نامشخص"
    if p.get("final_budget_usd"):
        price_line = f"{float(p['final_budget_usd']):.0f} دلار (بودجه نهایی)"
    elif p.get("budget_min_usd") or p.get("budget_max_usd"):
        price_line = (
            f"{p.get('budget_min_usd', '؟')} تا "
            f"{p.get('budget_max_usd', '؟')} دلار (بازه پیشنهادی)"
        )

    score_line = ""
    score = getattr(hit, "score", None)

    if score is not None:
        score_line = f"  شباهت: {score:.3f}\n"

    return (
        f"- عنوان: {p.get('title')}\n"
        f"  توضیحات: {p.get('description')}\n"
        f"  دسته‌بندی: {p.get('category')}\n"
        f"  مهارت‌ها: {', '.join(p.get('skills') or [])}\n"
        f"  مدت زمان: {p.get('duration_days')} روز\n"
        f"  قیمت: {price_line}\n"
        f"{score_line}"
    )


def build_llm_prompt(query: str, hits , usd_rate:float) -> str:
    comparables = "\n\n".join(format_comparable(h) for h in hits)
    prompt = f"""تو یک کارشناس قیمت‌گذاری پروژه‌های فریلنسری در پلتفرم‌های ایرانی (مثل کارلنسر و پونیشا) هستی.

درخواست کاربر برای یک پروژه جدید:
"{query}"

در ادامه چند پروژه مشابه واقعی که قبلاً قیمت‌گذاری شده‌اند آورده شده:

{comparables}


با توجه به پروژه‌های مشابه بالا:
1.  یک بازه قیمت پیشنهادی (حداقل و حداکثر، به دلار) برای پروژه جدید تخمین بزن ولی قیمتی که میدی رو اول بر اساس نرخ دلاری که بهت میدم به نومن تبدیل کن.
2. یک عدد نقطه‌ای (بهترین تخمین) هم ارائه بده.
3. در ۲ تا ۳ جمله توضیح بده چرا به این بازه رسیدی (بر اساس کدام پروژه‌های مشابه، مهارت‌ها، یا مدت زمان).

نرخ دلار: {usd_rate}


قوانین:
- estimated_price باید بهترین تخمین نقطه‌ای قیمت به تومن باشد.
- تخمینی که میزنی باید بیشتر نزدیک پایین بازه باشد
- reasoning باید کوتاه و شامل دلیل اصلی تخمین باشد.
- فقط JSON معتبر برگردان.
- هیچ متن دیگری خارج از JSON ننویس.

فرمت دقیق پاسخ:

{{
    "reasoning": "توضیح کوتاه درباره نحوه تخمین قیمت",
    "estimated_price": 1234.5
}}

پاسخ را به صورت خلاصه و ساخت‌یافته بده.
"""
    return prompt




llm_client = OpenAI(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_BASE_URL,
)

from itertools import islice

class Hit:
    def __init__(self, payload):
        self.payload = payload

def estimate(query: str, db: Session, rows = None ,top_k: int = TOP_K) -> dict:
    model = SentenceTransformer(settings.EMBEDDING_MODEL)
    client = get_client()
    hits = [None] * top_k
    if not rows:
        hits = search(query, model, client, top_k)
    else:
        hits = [Hit(row) for row in islice(rows, top_k)]

    usd_rate = get_current_usd_rate(db)

    similar_projects = []

    for hit in hits:
        row = hit.payload

        project = SimilarProject(
            final_budget=_num(row.get("final_budget")),
            final_budget_usd=_num(row.get("final_budget_usd")),
            usd_rate=usd_rate,

            min_budget=_num(row.get("budget_min")),
            max_budget=_num(row.get("budget_max")),
            min_budget_usd=_num(row.get("budget_min_usd")),
            max_budget_usd=_num(row.get("budget_max_usd")),

            organization=str(row.get("organization", "")),
            outer_link=str(row.get("outer_link", "")),
            scraped_project_id=str(row.get("scraped_project_id", "")),

            project_id=str(row.get("project_id", "")),
            title=str(row.get("title", "")),
            scraped_date_created=str(row.get("scraped_date_created", "")),
            description=str(row.get("description", "")),
            duration_days=_num(row.get("duration_days")),
        )

        similar_projects.append(
            _to_similar_project_result(project)
        )

    prompt = build_llm_prompt(query, hits , usd_rate)

    response = llm_client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
        temperature=0,
    )

    content = response.choices[0].message.content
    result = json.loads(content)

    return {
        "reasoning": str(result["reasoning"]),
        "estimated_price": float(result["estimated_price"]),
        "similar_projects":similar_projects
    }

def matching_projects(query: str, top_k: int = None):
    model = SentenceTransformer(settings.EMBEDDING_MODEL)
    client = get_client()
    hits = search(query, model, client, top_k)
    return hits


if __name__ == "__main__":
    user_query = " ".join(sys.argv[1:]) or "طراحی وبسایت فروشگاهی با React و درگاه پرداخت آنلاین"
    prompt = estimate(user_query)
    hits = matching_projects(user_query)
    print("=" * 60)
    print("RETRIEVED", len(hits), "COMPARABLE PROJECTS")
    print("=" * 60)
    for h in hits:
        print(f"[{h.score:.3f}] {h.payload.get('title')}")
    print()
    print("=" * 60)
    print("GENERATED PROMPT FOR LLM")
    print("=" * 60)
    print(prompt)
