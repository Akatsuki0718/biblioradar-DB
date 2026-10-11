import requests
import json

def collect_isbn_from_openbd():
    url = "https://api.openbd.jp/v1/coverage"
    print("OpenBDからISBN一覧を取得中…")

    isbns = requests.get(url).json()

    print(f"取得したISBN数: {len(isbns)}")

    with open("openbd_isbns.json", "w", encoding="utf-8") as f:
        json.dump(isbns, f, ensure_ascii=False, indent=2)

    return isbns

if __name__ == "__main__":
    collect_isbn_from_openbd()