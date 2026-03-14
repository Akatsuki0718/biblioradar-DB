import requests
import time
import os
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
import openai

# -------------------------
# .env 読み込み
# -------------------------
load_dotenv()

openai.api_key = os.getenv("OPENAI_API_KEY")

# Qdrant クライアント
qdrant = QdrantClient(
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY")
)

PROCESSED_FILE = "processed_isbns.txt"
LOG_FILE = "collector.log"

# -------------------------
# ログ出力
# -------------------------
def log(message):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(message + "\n")
    print(message)

# -------------------------
# 処理済みISBNの読み込み
# -------------------------
def load_processed():
    if not os.path.exists(PROCESSED_FILE):
        return set()
    with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f)

# -------------------------
# 処理済みISBNの保存
# -------------------------
def save_processed(isbn):
    with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
        f.write(isbn + "\n")

# -------------------------
# openBD から ISBN リスト取得
# -------------------------
def fetch_isbn_list():
    url = "https://api.openbd.jp/v1/coverage"
    return requests.get(url).json()

# -------------------------
# 1冊の処理
# -------------------------
def process_book(isbn):
    # --- openBD から書誌情報取得 ---
    url = f"https://api.openbd.jp/v1/get?isbn={isbn}"
    data = requests.get(url).json()

    if not data or data[0] is None:
        return

    summary = data[0]["summary"]
    title = summary.get("title", "")
    author = summary.get("author", "")
    publisher = summary.get("publisher", "")

    # --- 埋め込み生成 ---
    text = f"{title}\n{author}\n{publisher}"
    embedding = openai.embeddings.create(
        model="text-embedding-3-small",
        input=text
    ).data[0].embedding

    # --- Qdrant に保存 ---
    qdrant.upsert(
        collection_name="books",
        points=[
            PointStruct(
                id=int(isbn[-9:], 10),
                vector=embedding,
                payload={
                    "isbn": isbn,
                    "title": title,
                    "author": author,
                    "publisher": publisher
                }
            )
        ]
    )

# -------------------------
# メイン処理
# -------------------------
if __name__ == "__main__":
    log("=== Collector started ===")

    processed = load_processed()
    isbns = fetch_isbn_list()

    log(f"Total ISBNs from openBD: {len(isbns)}")
    log(f"Already processed: {len(processed)}")

    for isbn in isbns:

        if not isbn.startswith("9384"):
            continue

        if isbn in processed:
            continue

        try:
            process_book(isbn)
            save_processed(isbn)
            log(f"SUCCESS: {isbn}")

        except Exception as e:
            log(f"ERROR: {isbn} - {e}")

        time.sleep(1.5)

    log("=== Collector finished ===")