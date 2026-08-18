"""
Reads the projects CSV, builds an embedding text per row, and upserts
everything into a Qdrant collection with rich metadata payload.

Usage:
    python build_index.py
"""
import pandas as pd
from tqdm import tqdm
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from sentence_transformers import SentenceTransformer

from app.core.config import settings
from app.services.rag_system.common import build_document_text, build_payload, to_point_id, embed_text
from pathlib import Path


DATASET_PATH = Path(__file__).parent.parent.parent.parent / "ml_dataset" / "full_dataset.csv"
BATCH_SIZE = 64

def get_client() -> QdrantClient:
    if settings.QDRANT_URL:
        return QdrantClient(url=settings.QDRANT_URL , timeout=1000)



def main():
    print(f"Loading CSV from {DATASET_PATH} ...")
    df = pd.read_csv(DATASET_PATH)
    print(f"Loaded {len(df)} rows")

    print(f"Loading embedding model: {settings.EMBEDDING_MODEL} ...")
    model = SentenceTransformer(settings.EMBEDDING_MODEL)
    vector_size = model.get_embedding_dimension()

    client = get_client()

    if not client.collection_exists(settings.QDRANT_COLLECTION):
        print(f"Creating collection '{settings.QDRANT_COLLECTION}' (dim={vector_size})")
        client.create_collection(
            collection_name=settings.QDRANT_COLLECTION,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
    else:
        print(f"Collection '{settings.QDRANT_COLLECTION}' already exists, upserting into it")

    rows = df.to_dict(orient="records")
    doc_texts = [build_document_text(r) for r in rows]

    print("Embedding documents...")
    prefixed_texts = [embed_text(model, t, is_query=False) for t in doc_texts]
    embeddings = model.encode(
        prefixed_texts,
        batch_size=BATCH_SIZE,
        show_progress_bar=True,
        normalize_embeddings=True,
    )

    print("Upserting into Qdrant...")
    points = []
    for row, text, emb in zip(rows, doc_texts, embeddings):
        point_id = to_point_id(row.get("project_id"))
        payload = build_payload(row, text)
        points.append(PointStruct(id=point_id, vector=emb.tolist(), payload=payload))

    for i in tqdm(range(0, len(points), BATCH_SIZE)):
        batch = points[i : i + BATCH_SIZE]
        client.upsert(collection_name=settings.QDRANT_COLLECTION, points=batch)

    count = client.count(collection_name=settings.QDRANT_COLLECTION).count
    print(f"Done. Collection '{settings.QDRANT_COLLECTION}' now has {count} points.")


if __name__ == "__main__":
    main()
