from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance
from config import QDRANT_HOST, QDRANT_PORT, QDRANT_COLLECTION

def init_collection():
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    client.recreate_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config={
            "title_vector": VectorParams(size=384, distance=Distance.COSINE),
            "summary_vector": VectorParams(size=384, distance=Distance.COSINE),
        }
    )

    return client

def upload_records(client, records):
    points = []
    for rec in records:
        points.append({
            "id": rec["id"],
            "payload": rec["payload"],
            "vector": {
                "title_vector": rec["title_vector"],
                "summary_vector": rec["summary_vector"],
            }
        })

    client.upsert(collection_name=QDRANT_COLLECTION, points=points)