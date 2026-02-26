import os
import requests
import uuid
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from openai import OpenAI

load_dotenv()

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

qdrant = QdrantClient(
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY")
)

collection_name = "books"


# --- description を 300文字に整形 ---
def shorten_description(text: str) -> str:
    if len(text) <= 300:
        return text

    prompt = f"次の文章を300文字以内に日本語で要約してください：\n{text}"

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content.strip()


# --- Embedding ---
def embed(text: str):
    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding


import requests

def fetch_book(isbn: str):
    url = f"https://api.openbd.jp/v1/get?isbn={isbn}"
    data = requests.get(url).json()

    if not data or data[0] is None:
        return None

    info = data[0]["summary"]

    title = info.get("title", "")
    author = info.get("author", "")
    publisher = info.get("publisher", "")
    pubdate = info.get("pubdate", "")
    description = info.get("description", "")

    # summary が無い場合は疑似説明文を作る
    if not description:
        description = f"{title}（著者: {author}, 出版社: {publisher}, 出版日: {pubdate}）の書籍情報です。"

    return {
        "title": title,
        "author": author,
        "description": description
    }


# --- Qdrant に保存 ---
def save_to_qdrant(isbn, title, author, description, vector):
    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, isbn))  # ← UUID に変換（絶対に安全）

    qdrant.upsert(
        collection_name=collection_name,
        points=[
            PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "isbn": isbn,
                    "title": title,
                    "author": author,
                    "description": description
                }
            )
        ]
    )


# --- メイン処理 ---
def process_book(isbn):
    isbn = str(isbn)

    book = fetch_book(isbn)
    if not book:
        print(f"Not found: {isbn}")
        return

    desc = book["description"] or "この本の説明文はありません。"
    desc = shorten_description(desc)

    vector = embed(desc)

    save_to_qdrant(isbn, book["title"], book["author"], desc, vector)

    print(f"Saved: {book['title']} ({isbn})")


if __name__ == "__main__":
    sample_isbns = [
        "9784101010014",
        "9784041026229",
        "9784167905889",
    ]

    for isbn in sample_isbns:
        process_book(isbn)