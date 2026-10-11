# Google Books API（必要なら後で使う）
GOOGLE_BOOKS_API_URL = "https://www.googleapis.com/books/v1/volumes"

# Qdrant 設定
QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
QDRANT_COLLECTION = "books"   # main.py と合わせる

# SentenceTransformer の出力次元
VECTOR_DIM = 384