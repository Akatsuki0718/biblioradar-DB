from sentence_transformers import SentenceTransformer

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

def embed_title(title: str):
    return model.encode(title).tolist()

def embed_summary(summary: str):
    return model.encode(summary).tolist()