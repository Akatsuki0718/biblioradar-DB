from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams,
    Distance,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    HnswConfig
)
import os
from dotenv import load_dotenv

load_dotenv()

client = QdrantClient(
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY")
)

collection_name = "books"

client.recreate_collection(
    collection_name=collection_name,
    vectors_config=VectorParams(
        size=1536,
        distance=Distance.COSINE,
        quantization_config=ScalarQuantization(
            scalar=ScalarQuantizationConfig(
                type=ScalarType.INT8,
                quantile=0.99,
                always_ram=False
            )
        )
    ),
    hnsw_config=HnswConfig(
        m=8,
        ef_construct=64,
        full_scan_threshold=0
    )
)

print("Qdrant collection created with INT8 quantization + lightweight HNSW!")