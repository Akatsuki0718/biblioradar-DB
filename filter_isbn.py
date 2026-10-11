def filter_japanese_books_with_isbn(books):
    result = []
    for b in books:
        isbn = b.get("isbn")
        if isbn and isbn.startswith("9784"):
            result.append(b)
    return result