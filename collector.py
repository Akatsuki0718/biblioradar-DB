import requests
import os
import time
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

qdrant = QdrantClient(
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY")
)

PROCESSED_FILE = "processed_isbns.txt"
LOG_FILE = "collector.log"

def log(msg: str):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg)

def load_processed():
    if not os.path.exists(PROCESSED_FILE):
        return set()
    with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f)

def save_processed(isbn: str):
    with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
        f.write(isbn + "\n")

def fetch_isbn_list():
    url = "https://api.openbd.jp/v1/coverage"
    return requests.get(url).json()

def get_book_data(isbn: str):
    url = f"https://api.openbd.jp/v1/get?isbn={isbn}"
    data = requests.get(url).json()
    if not data or data[0] is None:
        return None

    summary = data[0]["summary"]
    title = summary.get("title", "")
    author = summary.get("author", "")
    publisher = summary.get("publisher", "")
    description = summary.get("description", "")

    return {
        "isbn": isbn,
        "title": title,
        "author": author,
        "publisher": publisher,
        "summary": description
    }

def generate_summary_if_needed(book):
    if book["summary"]:
        return book["summary"]

    prompt = f"次の本のタイトルと著者から、内容を想像して日本語で200文字程度の要約を書いてください。\n\nタイトル: {book['title']}\n著者: {book['author']}"
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "あなたは日本語の本の要約を作るアシスタントです。"},
            {"role": "user", "content": prompt}
        ]
    )
    return resp.choices[0].message.content.strip()

def embed(text: str):
    resp = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return resp.data[0].embedding

def process_book(isbn: str):
    book = get_book_data(isbn)
    if not book or not book["title"]:
        log(f"[SKIP] 書誌情報なし: {isbn}")
        return

    summary_text = generate_summary_if_needed(book)
    book["summary"] = summary_text

    title_text = f"{book['title']}\n{book['author']}\n{book['publisher']}"
    title_vec = embed(title_text)
    summary_vec = embed(summary_text)

    qdrant.upsert(
        collection_name="books",
        points=[
            PointStruct(
                id=int(isbn[-9:], 10),
                vector={
                    "title_vector": title_vec,
                    "summary_vector": summary_vec
                },
                payload=book
            )
        ]
    )
    log(f"[OK] {isbn} {book['title']}")

if __name__ == "__main__":
    log("=== New Collector started ===")
    processed = load_processed()
    isbns = fetch_isbn_list()

    for isbn in isbns:
        if len(isbn) != 13 or not isbn.isdigit():
            continue
        if not isbn.startswith("9784"):
            continue
        if isbn in processed:
            continue

        try:
            process_book(isbn)
            save_processed(isbn)
        except Exception as e:
            log(f"[ERROR] {isbn} - {e}")

        time.sleep(1.0)

    log("=== New Collector finished ===")