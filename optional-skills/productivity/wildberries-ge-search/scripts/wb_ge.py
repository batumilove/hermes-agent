#!/usr/bin/env python3
"""Search Wildberries Georgia through its browser-derived JSON endpoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINT = "https://www.wildberries.ge/__internal/u-search/exactmatch/sng/common/v18/search"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def token_from(args: argparse.Namespace) -> str:
    if args.token_file:
        path = Path(args.token_file).expanduser()
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise SystemExit("Refusing token file: permissions must be 0600 or stricter")
        token = path.read_text(encoding="utf-8").strip()
    else:
        token = os.environ.get("WB_X_WBAAS_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "Missing anti-bot session token. Set WB_X_WBAAS_TOKEN or pass "
            "--token-file PATH containing only the x_wbaas_token cookie value."
        )
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in token):
        raise SystemExit("Refusing anti-bot session token containing control characters.")
    return token


def search(query: str, page: int, sort: str, token: str) -> dict:
    params = {
        "ab_testing": "false",
        "appType": "1",
        "curr": "gel",
        "dest": "123586302",
        "hide_dflags": "131072",
        "hide_dtype": "11;13;15",
        "hide_vflags": "4294967296",
        "inheritFilters": "true",
        "lang": "ka",
        "locale": "ge",
        "page": str(page),
        "query": query,
        "resultset": "catalog",
        "sort": sort,
        "spp": "30",
        "suppressSpellcheck": "false",
    }
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Language": "ka-GE,ka;q=0.9,en;q=0.8",
            "Cookie": "x_wbaas_token=" + token,
            "Referer": "https://www.wildberries.ge/",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 498:
            raise SystemExit(
                "Wildberries rejected the anti-bot session (HTTP 498). "
                "Capture a fresh x_wbaas_token in a real browser."
            ) from None
        raise SystemExit(f"Wildberries HTTP error: {exc.code}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"Wildberries request failed: {exc.reason}") from None


def validate_response(data: object) -> dict:
    """Reject routing-only or malformed HTTP-200 responses."""
    if not isinstance(data, dict):
        raise SystemExit(
            "Unexpected response schema from Wildberries; "
            "required metadata/products/total fields are missing or invalid."
        )
    total = data.get("total")
    valid = (
        isinstance(data.get("metadata"), dict)
        and isinstance(data.get("products"), list)
        and isinstance(total, int)
        and not isinstance(total, bool)
        and total >= 0
    )
    if not valid:
        raise SystemExit(
            "Unexpected response schema from Wildberries; "
            "required metadata/products/total fields are missing or invalid."
        )
    return data


def normalized_product(product: dict) -> dict:
    size = next((x for x in product.get("sizes", []) if x.get("price")), {})
    price = size.get("price", {})
    return {
        "id": product.get("id"),
        "brand": product.get("brand") or "",
        "name": product.get("name") or "",
        "price_gel": round(price.get("product", 0) / 100, 2) if price.get("product") is not None else None,
        "original_price_gel": round(price.get("basic", 0) / 100, 2) if price.get("basic") is not None else None,
        "rating": product.get("reviewRating"),
        "reviews": product.get("feedbacks"),
        "url": f"https://www.wildberries.ge/catalog/{product.get('id')}/detail.aspx",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Search Wildberries.ge and return GEL-priced products.")
    parser.add_argument("query", help="product search text")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--sort", default="popular", choices=("popular", "pricedown", "priceup", "rate", "newly"))
    parser.add_argument("--token-file", help="mode-600 file containing the x_wbaas_token cookie value")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()
    if args.page < 1:
        parser.error("--page must be at least 1")

    data = validate_response(search(args.query, args.page, args.sort, token_from(args)))
    products = [normalized_product(p) for p in data["products"][: args.limit]]
    result = {"query": args.query, "page": args.page, "total": data["total"], "products": products}
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    print(f"{result['total']} results for {args.query!r} (page {args.page})")
    for item in products:
        price = "? GEL" if item["price_gel"] is None else f"{item['price_gel']:.2f} GEL"
        print(f"{item['id']}  {price:>12}  {item['brand']} — {item['name']}")
        print(f"  {item['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
