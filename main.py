from fastapi import FastAPI
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from openai import OpenAI
import os

load_dotenv()

app = FastAPI()

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

qdrant = QdrantClient(
    url=os.getenv("QDRANT_URL"),
    api_key=os.getenv("QDRANT_API_KEY")
)

collection_name = "books"


def embed(text: str):
    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding


@app.get("/recommend")
def recommend(query: str):
    vector = embed(query)

    result = qdrant.query_points(
        collection_name=collection_name,
        query=vector,
        limit=5
    )

    return [
        {
            "isbn": r.payload.get("isbn"),
            "title": r.payload.get("title"),
            "author": r.payload.get("author"),
            "description": r.payload.get("description"),
            "score": r.score,
        }
        for r in result.points
    ]