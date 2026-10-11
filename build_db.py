import json
import requests
import time
from sentence_transformers import SentenceTransformer
from fetch_books import collect_books
from config import QDRANT_HOST, QDRANT_PORT

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

BATCH_SIZE = 1000


def create_collection(name):
    url = f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/{name}"
    res = requests.put(url, json={
        "vectors": {
            "size": 384,
            "distance": "Cosine",
            "on_disk": True
        }
    })
    print(f"Create {name}:", res.json())


def upsert_points(name, points):
    url = f"http://{QDRANT_HOST}:{QDRANT_PORT}/collections/{name}/points?wait=true"
    res = requests.put(url, json={"points": points})
    print(f"Upsert {name}: {res.json()}")


def build_db():
    print("=== DB BUILD START ===")

    # ★ 大量収集
    books = collect_books()
    total = len(books)
    print(f"Collected books: {total}")

    # ★ コレクション作成
    create_collection("books_title")
    create_collection("books_summary")

    # ★ バッチ処理
    for start in range(0, total, BATCH_SIZE):
        end = min(start + BATCH_SIZE, total)
        batch = books[start:end]

        print(f"Processing batch {start} → {end} / {total}")

        title_points = []
        summary_points = []

        for idx, book in enumerate(batch, start=start):
            # タイトルベクトル
            title_vec = model.encode(book["title"]).tolist()

            # 説明文ベクトル
            desc = book.get("description", "")
            summary_vec = model.encode(desc).tolist() if desc.strip() else [0.0] * 384

            title_points.append({
                "id": idx,
                "vectors": title_vec,
                "payload": book
            })

            summary_points.append({
                "id": idx,
                "vectors": summary_vec,
                "payload": book
            })

        # ★ Qdrant にアップロード
        upsert_points("books_title", title_points)
        upsert_points("books_summary", summary_points)

        time.sleep(0.1)

    print("=== DB BUILD DONE ===")


if __name__ == "__main__":
    build_db()