from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, ScalarQuantization, ScalarQuantizationConfig
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
        size=1536,              # text-embedding-3-small
        distance=Distance.COSINE
    ),
    quantization_config=ScalarQuantization(
        scalar=ScalarQuantizationConfig(
            type="int8",        # 8bit量子化
            quantile=0.99,
            always_ram=True
        )
    )
)

print("Qdrant collection created with quantization!")