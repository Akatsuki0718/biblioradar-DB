def build_records(items):
    records = []
    for idx, item in enumerate(items):
        records.append({
            "id": idx,  # ★ Qdrant が受け付ける整数IDに変更
            "payload": {
                "title": item.get("title", ""),
                "authors": item.get("authors", []),
                "publisher": item.get("publisher", ""),
                "description": item.get("description", ""),
                "isbn": item["isbn"],  # ISBN は payload に残す
            }
        })
    return records