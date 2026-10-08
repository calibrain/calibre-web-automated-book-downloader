# API access with an API key

Shelfmark's web interface is driven entirely by a JSON API under `/api/`. Set
the `SHELFMARK_API_KEY` environment variable and scripts, dashboards and assistants can
call the same API without a browser session. Browser logins keep working
exactly as before: it is cookie **or** key.

## Set the key

```yaml
environment:
  SHELFMARK_API_KEY: "a-long-random-secret"
```

Generate something long and random (for example `openssl rand -base64 32`).
A request carrying the key acts as an **admin**: the first admin user in
Shelfmark's user database. Create an admin before relying on the key in any
install that has none yet (for example an OIDC-only install). Without an
admin user, the key still authenticates as an admin identity with no user
row, and routes that need one (requests, activity) answer 403. To rotate,
change the variable and restart. Unset it and the feature is off. When the
instance runs with no authentication configured (`AUTH_METHOD=none`), the
key is simply unnecessary.

## Send the key

Either header works, and both are checked, so the key can be sent in
`X-Api-Key` behind a reverse proxy that sets its own `Authorization` header.

```bash
curl -s -H "Authorization: Bearer $SHELFMARK_API_KEY" https://shelfmark.example.com/api/downloads/active
curl -s -H "X-Api-Key: $SHELFMARK_API_KEY" https://shelfmark.example.com/api/downloads/active
```

A request that carries the key is authenticated by the key alone. Session
cookies are ignored and none are set. A bearer value that is not the configured
key is ignored and the request continues with normal session authentication,
so reverse proxies that forward their own tokens are unaffected; without a valid
session such a request gets the usual `401 {"error": "Unauthorized"}`. A
database error while resolving the admin returns
`500 {"error": "Authentication error"}` — never anonymous access.
`/api/auth/check` reflects the browser session only and ignores the key, so
use `/api/status` to verify a key.

## Examples

Search, then look up releases, then queue one (the same calls the web UI makes):

```bash
curl -s -H "Authorization: Bearer $SHELFMARK_API_KEY" \
  "https://shelfmark.example.com/api/metadata/search?query=dune%20frank%20herbert"
# -> {"books":[{"provider":"hardcover","provider_id":"427363", ...}]}

curl -s -H "Authorization: Bearer $SHELFMARK_API_KEY" \
  "https://shelfmark.example.com/api/releases?provider=hardcover&book_id=427363&content_type=ebook"
# -> {"releases":[{"source":"direct_download","source_id":"...", ...}], ...}

curl -s -X POST -H "Authorization: Bearer $SHELFMARK_API_KEY" -H "Content-Type: application/json" \
  -d @release.json https://shelfmark.example.com/api/releases/download
# release.json = one object from "releases" (source and source_id are required)

curl -s -H "Authorization: Bearer $SHELFMARK_API_KEY" https://shelfmark.example.com/api/status
```

## Security notes

- The key is compared in constant time and is never logged.
- Keyed requests never set cookies and ignore any cookie sent with them.
- WebSocket (live activity) connections do not accept the key; poll `/api/status` instead.
- The key is a root-equivalent credential: an admin can configure a custom
  post-download script that the server executes, so treat it like a root
  password and send it only over HTTPS.

## Read-only key and dashboard stats

Set `SHELFMARK_API_KEY_READONLY` to a second secret for dashboards and monitors. It can read one endpoint,
`GET /api/stats`, and nothing else. Writes and every other path return 403, and the key never gets a session.
The admin key reaches `/api/stats` too.

```bash
curl -s -H "X-Api-Key: $SHELFMARK_API_KEY_READONLY" https://shelfmark.example.com/api/stats
```

The response has counts only: books added in the last 7 and 30 days by format, the queue, requests by outcome,
and download failures in the last 7 days. No titles and no user names.
