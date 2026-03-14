from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams,
    Distance,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType
)
import os
from dotenv import load_dotenv

load_dotenv()

client = QdrantClient(
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY")
)

collection_name = "books"

# 既存コレクション削除
if client.collection_exists(collection_name):
    client.delete_collection(collection_name)

# 新規作成（HNSW設定なし → クラウド側のデフォルトが使われる）
client.create_collection(
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
    )
)

print("Qdrant collection created with INT8 quantization (HNSW = cloud default)")