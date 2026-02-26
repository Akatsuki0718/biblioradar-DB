import requests
from prepare_data import process_book

def fetch_isbn_list():
    url = "https://api.openbd.jp/v1/coverage"
    return requests.get(url).json()

if __name__ == "__main__":
    isbns = fetch_isbn_list()

    for isbn in isbns:
        process_book(isbn)