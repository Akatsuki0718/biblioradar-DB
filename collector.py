import requests
from prepare_data import process_book
import time
import os

PROCESSED_FILE = "processed_isbns.txt"
LOG_FILE = "collector.log"


def log(message: str):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(message + "\n")
    print(message)


def load_processed():
    if not os.path.exists(PROCESSED_FILE):
        return set()
    with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f.readlines())


def save_processed(isbn: str):
    with open(PROCESSED_FILE, "a", encoding="utf-8") as f:
        f.write(isbn + "\n")


def fetch_isbn_list():
    url = "https://api.openbd.jp/v1/coverage"
    return requests.get(url).json()


if __name__ == "__main__":
    log("=== Collector started ===")

    processed = load_processed()
    isbns = fetch_isbn_list()

    log(f"Total ISBNs from openBD: {len(isbns)}")
    log(f"Already processed: {len(processed)}")

    for isbn in isbns:
        if isbn in processed:
            continue

        try:
            process_book(isbn)
            save_processed(isbn)
            log(f"SUCCESS: {isbn}")
        except Exception as e:
            log(f"ERROR: {isbn} - {e}")

        time.sleep(0.5)  # API負荷軽減

    log("=== Collector finished ===")