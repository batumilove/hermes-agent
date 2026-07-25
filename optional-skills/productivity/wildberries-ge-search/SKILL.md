---
name: wildberries-ge-search
version: 1.0.0
description: Search Wildberries.ge products with GEL prices.
---

# Wildberries.ge Search

Search the Georgian Wildberries catalog through its browser-derived JSON API. Product requests are browserless after a real browser supplies a valid anonymous `x_wbaas_token` anti-bot cookie.

## When to Use

Use for:

- Wildberries Georgia product discovery and comparison.
- GEL prices, original prices, ratings, review counts, product IDs, and links.
- Machine-readable search results for later analysis.
- Diagnosing HTTP 498 or an empty direct-backend response.

This skill is read-only. Use a shopping/checkout skill plus explicit approval for login, cart, favorite, review, order, or purchase mutations.

## Prerequisites

- Python 3.11+; the CLI uses only the standard library.
- A valid anonymous `x_wbaas_token` cookie from a successful real-browser Wildberries.ge session.
- Store the token in a file with mode `0600` or stricter, or inject it through `WB_X_WBAAS_TOKEN` without placing it in shell history.

## How to Run

From this skill directory:

```bash
python3 scripts/wb_ge.py "lego technic" \
  --limit 10 \
  --token-file ~/.config/wildberries-ge/x_wbaas_token
```

JSON output:

```bash
python3 scripts/wb_ge.py "robot vacuum" \
  --sort pricedown \
  --limit 20 \
  --json \
  --token-file ~/.config/wildberries-ge/x_wbaas_token
```

Supported sorts: `popular`, `pricedown`, `priceup`, `rate`, and `newly`.

## Procedure

1. Check for an existing user-approved mode-`0600` token file or securely injected environment value. Never print it.
2. Run `scripts/wb_ge.py` with the requested query, page, limit, and sort.
3. On HTTP 200, require response keys `metadata`, `products`, and `total` before using the result.
4. Normalize `sizes[].price.product` and `sizes[].price.basic` from integer hundredths to GEL decimals.
5. Return only the normalized fields: `id`, `brand`, `name`, `price_gel`, `original_price_gel`, `rating`, `reviews`, and `url`.
6. If the request returns HTTP 498, follow `references/token-bootstrap.md` and retry exactly once with a fresh token.
7. If a direct backend returns routing metadata but no `products`, do not report that as a successful catalog search.

## Quick Reference

```text
Endpoint: GET /__internal/u-search/exactmatch/sng/common/v18/search
Locale:   locale=ge, lang=ka
Currency: curr=gel
Default destination observed: dest=123586302 (Tbilisi)
Success:  metadata + products + total
Expired/missing browser state: HTTP 498
```

JSON schema:

```text
query, page, total
products[]:
  id, brand, name
  price_gel, original_price_gel
  rating, reviews, url
```

## Security

- Treat `x_wbaas_token`, all cookies, HAR files, challenge payloads, captured headers, and signed product payload fields as secrets.
- Never print, paste into chat, commit, or include token values in tool output.
- Token files broader than `0600` are rejected by the CLI. Prefer token files over environment injection because same-UID processes may inspect process environments.
- Never expose `sizes[].payload`; it is signed opaque data and is unnecessary for search results.
- Do not capture account cookies unless the user explicitly requests an authenticated workflow.
- Securely remove temporary token, HAR, private-key, and derived-response files after validation unless the user explicitly approves persistence.

## Pitfalls

- Local headless Chromium may be detected and receive HTTP 498. Do not loop retries; switch to a successful normal browser session.
- The public backend may return only shard/routing metadata with no products.
- A green browser page is not proof that browserless replay works; verify the CLI receives HTTP 200 and product JSON.
- Wildberries can change endpoint versions, destinations, schema, or token behavior without notice.
- Product prices and totals are live and can change between requests.

## Verification

Credential-free repository tests:

```bash
scripts/run_tests.sh tests/skills/test_wildberries_ge_search_skill.py -q
python3 -m py_compile optional-skills/productivity/wildberries-ge-search/scripts/wb_ge.py
```

Live verification requires all of:

- HTTP 200 from the internal search endpoint.
- Keys `metadata`, `products`, and `total`.
- At least one normalized product for a broad known query.
- No token or raw signed `payload` in logs/output.

Report local tests, live HTTP status, returned count, and any HTTP 498/token-renewal caveat separately.
