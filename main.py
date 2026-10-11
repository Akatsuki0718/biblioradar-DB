from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
import requests
from config import QDRANT_HOST, QDRANT_PORT, QDRANT_COLLECTION

app = FastAPI()

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

class BookQuery(BaseModel):
    title: str
    author: str | None = None

@app.post("/recommend")
def recommend(req: BookQuery):
    # ① タイトル＋著者で検索用テキストを作る
    query_text = f"{req.title} {req.author or ''}"
    query_vec = model.encode(query_text).tolist()

    # ② タイトルコレクションで検索（books_title）
    hits = requests.post(
        f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/books_title/points/search",
        json={
            "vector": query_vec,
            "limit": 1
        }
    ).json()

    print("TITLE SEARCH RAW RESPONSE:", hits)

    raw = hits.get("result") or hits.get("points") or hits.get("hits") or []
    if not raw:
        return {"identified_book": None, "books": []}

    # ★ payload が無いので id を使う
    identified_id = raw[0]["id"]

    # ③ id を使って payload を取得（scroll）
    detail = requests.post(
        f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/books_title/points/scroll",
        json={"limit": 1, "offset": identified_id}
    ).json()

    print("TITLE DETAIL RAW RESPONSE:", detail)

    detail_raw = detail["result"]["points"][0]
    identified = detail_raw["payload"]

    # ④ 説明文ベクトルを作る
    desc = identified.get("description", "")
    summary_vec = model.encode(desc).tolist() if desc.strip() else [0.0] * 384

    # ⑤ 説明コレクションで類似検索（books_summary）
    similar = requests.post(
        f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/books_summary/points/search",
        json={
            "vector": summary_vec,
            "limit": 20
        }
    ).json()

    print("SIMILAR SEARCH RAW RESPONSE:", similar)

    raw_sim = similar.get("result") or similar.get("points") or similar.get("hits") or []
    books = []

    for p in raw_sim:
        sid = p["id"]
        detail2 = requests.post(
            f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/books_summary/points/scroll",
            json={"limit": 1, "offset": sid}
        ).json()

        payload = detail2["result"]["points"][0]["payload"]

        if payload["isbn"] != identified["isbn"]:
            books.append(payload)

    return {"identified_book": identified, "books": books[:10]}