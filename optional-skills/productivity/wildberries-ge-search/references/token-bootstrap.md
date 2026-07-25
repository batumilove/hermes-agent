# Secure Anonymous Token Bootstrap

Wildberries.ge protects its internal search endpoint with an anonymous `x_wbaas_token` cookie. A valid cookie produces product JSON; absent or expired state returns HTTP 498.

## Preferred: User-Controlled Browser Export

1. Open `https://www.wildberries.ge/` in a normal browser and complete any challenge.
2. Open developer tools → Application/Storage → Cookies → `https://www.wildberries.ge`.
3. Copy only `x_wbaas_token` directly into a local file. Do not paste it into chat.
4. Lock the directory and file before use:

   ```bash
   mkdir -p ~/.config/wildberries-ge
   chmod 700 ~/.config/wildberries-ge
   chmod 600 ~/.config/wildberries-ge/x_wbaas_token
   ```

5. Run `scripts/wb_ge.py ... --token-file ~/.config/wildberries-ge/x_wbaas_token`.

## Agent-Only: Encrypted Envelope

Use only when browser state and the local filesystem cannot share a cookie jar directly.

1. Generate an ephemeral local RSA-OAEP keypair; keep the private key in a mode-`0600` temporary file.
2. Send only the public key into the browser execution context.
3. In browser JavaScript:
   - Read only `x_wbaas_token`.
   - Generate a fresh AES-256-GCM key and 96-bit IV.
   - Encrypt the cookie value with AES-GCM.
   - Wrap the AES key with the ephemeral RSA public key.
   - Return only wrapped key, IV, ciphertext, and plaintext length.
4. Locally unwrap and decrypt directly into a mode-`0600` token file without printing plaintext.
5. Validate one browserless request.
6. Securely remove token, private key, HAR, and derived temporary output unless the user explicitly approves persistence.

Never return an unwrapped AES key with ciphertext; that is equivalent to exposing the token. Never request or print `document.cookie` wholesale.

## Bounded Browser Policy

- One navigation to a broad search page is enough.
- Resource URLs may be inspected to identify the endpoint; URLs are not credentials.
- At most one cookie-name-only check may confirm `x_wbaas_token` exists.
- Never return cookie values through browser tools.
- If local headless Chromium receives HTTP 498, do not loop retries. Switch to a successful normal browser session or report the blocker.

## Endpoint Contract

```text
GET https://www.wildberries.ge/__internal/u-search/exactmatch/sng/common/v18/search
```

Important parameters:

- `curr=gel`
- `dest=123586302` (Tbilisi destination observed during derivation)
- `lang=ka`
- `locale=ge`
- `query=<search text>`
- `resultset=catalog`
- `sort=popular`
- `spp=30`

The observed response contains `metadata`, `products`, and `total`. Product prices are nested in `sizes[].price`. Never expose signed `sizes[].payload` fields.

## Failure Meanings

- HTTP 498: missing, expired, or rejected anti-bot session.
- HTTP 200 with routing metadata and no `products`: direct public backend route, not the browser-facing combined response.
- HTTP 200 with `metadata`, `products`, and `total`: usable live result.
