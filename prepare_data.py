import os
import requests
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


def shorten_description(text: str) -> str:
    if len(text) <= 300:
        return text

    prompt = f"次の文章を300文字以内に日本語で要約してください：\n{text}"

    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content.strip()


def embed(text: str):
    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding


def fetch_book(isbn: str):
    api_key = os.getenv("GOOGLE_BOOKS_API_KEY")
    url = (
        "https://www.googleapis.com/books/v1/volumes"
        f"?q=isbn:{isbn}&key={api_key}&maxResults=1"
    )

    data = requests.get(url).json()

    if "items" not in data:
        return None

    info = data["items"][0]["volumeInfo"]

    return {
        "title": info.get("title", ""),
        "author": ", ".join(info.get("authors", [])),
        "description": info.get("description", "")
    }


def save_to_qdrant(isbn, title, author, description, vector):
    qdrant.upsert(
        collection_name=collection_name,
        points=[
            PointStruct(
                id=str(isbn),   # ← ここを文字列にする
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

def process_book(isbn):
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