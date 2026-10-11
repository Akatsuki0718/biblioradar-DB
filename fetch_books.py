import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://www.googleapis.com/books/v1/volumes"
API_KEY = os.getenv("GOOGLE_BOOKS_API_KEY")

if not API_KEY:
    print("ERROR: .env に GOOGLE_BOOKS_API_KEY が設定されていません。")
    exit()

def fetch_book_info(isbn):
    print(f"=== ISBN: {isbn} の検索開始 ===")

    # --- STEP 1: openBD から基本情報を取得 ---
    openbd_url = f"https://api.openbd.jp/v1/get?isbn={isbn}"
    res = requests.get(openbd_url)
    data = res.json()

    if not data or data[0] is None:
        print("【ヒットなし】 openBDに書籍データが存在しません。")
        print("-" * 40)
        return None

    book_data = data[0]
    summary = book_data.get("summary", {})
    title = summary.get("title", "")
    author = summary.get("author", "")

    # openBDから詳細なあらすじを取得試行
    description = ""
    onix = book_data.get("onix", {})
    collateral = onix.get("CollateralDetail", {})
    text_content = collateral.get("TextContent", [])

    for text in text_content:
        if text.get("TextType") == "03":
            description = text.get("Text", "")
            break

    print(f"  - タイトル (openBD): {title}")
    print(f"  - 著者 (openBD): {author}")

    if description:
        print(f"  - あらすじ (openBDより取得):\n{description}")
        print("-" * 40)
        return {"title": title, "author": author, "description": description}

    # --- STEP 2: openBDであらすじが無い場合、Google Books APIで検索 ---
    print("  ※ openBDにあらすじが無いため、Google Books APIから補完します...")

    # タイトルと著者名のクレンジング
    clean_title = re.sub(r'[.｡。.上中下巻]+.*$', '', title)
    clean_title = clean_title.replace('小説', '').strip()
    clean_author = re.sub(r'[,，\d\-]+', '', author).strip()

    # ★ intitle: を使ってタイトルを厳密にしつつ、日本語限定(langRestrict=ja)で複数件取得
    params = {
        "q": f"intitle:{clean_title} {clean_author}",
        "langRestrict": "ja",
        "key": API_KEY,
        "maxResults": 5,  # 複数候補を取得して、解説本を弾けるようにする
        "printType": "books"
    }

    try:
        g_res = requests.get(API_URL, params=params)
        if g_res.status_code == 200:
            g_data = g_res.json()
            items = g_data.get("items", [])

            selected_volume = None
            # 候補の中から「解説」「論」「ガイド」「研究」などの文字が入っていない本を優先して選ぶ
            for item in items:
                v_info = item.get("volumeInfo", {})
                v_title = v_info.get("title", "")

                # 除外したいキーワード
                if any(ng in v_title for ng in ["解説", "論", "ガイド", "研究", "秘密"]):
                    continue

                selected_volume = v_info
                break

            # もし全てに除外ワードが入っている場合は、最初の候補をフォールバックとして使う
            if not selected_volume and items:
                selected_volume = items[0].get("volumeInfo", {})

            if selected_volume:
                description = selected_volume.get("description", "あらすじ情報は見つかりませんでした。")
                print(f"  - 採用された書籍名: {selected_volume.get('title')}")
                print(f"  - あらすじ (Google Booksより取得):\n{description}")
            else:
                print("  - あらすじ: Google Booksでも見つかりませんでした。")
        else:
            print(f"  - Google API エラー: {g_res.status_code}")

    except Exception as e:
        print(f"  - Google API 通信エラー: {e}")

    print("-" * 40)
    return {"title": title, "author": author, "description": description}


# テスト実行
test_isbns = [
    "9784101001548",  # 海辺のカフカ 上巻
    "9784041026229",  # 小説君の名は。
    "9784167906055"   # 未登録のISBN
]

for isbn in test_isbns:
    fetch_book_info(isbn)