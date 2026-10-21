# Shelfmark E2E Docker Testing Platform

A hermetic, container-based end-to-end platform that boots the real Shelfmark app
against **controllable** dependencies — a fake Anna's Archive, a Cloudflare gate, a
mock FlareSolverr bypasser, a mock Prowlarr + a real qBittorrent, custom DNS
servers, HTTP/SOCKS5 proxies, and a Tor profile — and runs a cluster test suite
under each **config profile**.

It exists to make the recurring bug clusters from the issue/PR analysis impossible
to reintroduce silently. The biggest one — Tor/Cloudflare/bypasser (37 issues /
67 fix PRs) — had almost no automated coverage; this platform changes that.

```
  pytest suite (host :8084)
        │
        ▼
  shelfmark (under test) ── egress depends on the active profile:
        ├─ direct ──────────────► mock-aa (.10)                  fake Anna's Archive
        ├─ Cloudflare gate ─────► mock-cf (.11) ─► mock-aa       [full: real Chrome solves it]
        ├─ FlareSolverr ────────► mock-cf (.11) ─► mock-flaresolverr (.12)  [bypasser-external]
        ├─ custom DNS ──────────► coredns (.20) / coredns-blocked (.22)     [dns-manual/blocked]
        ├─ HTTP / SOCKS proxy ──► tinyproxy (.30) / microsocks (.31)        [proxy-http/socks]
        ├─ Tor (transparent) ───► in-image tor.sh                           [tor]
        └─ Prowlarr → client ───► mock-prowlarr (.40) ─► qBittorrent        [full: real download]

  (all on one e2e docker network, 172.30.0.0/24, static IPs for DNS determinism)
```

## Quick start

```bash
# one profile
make e2e-platform                       # baseline
make e2e-platform-profile PROFILE=bypasser-external
make e2e-platform-profile PROFILE=client-deluge
make e2e-platform-full                  # heavy: real Chrome + DoH + real qBittorrent

# the whole matrix
make e2e-platform-matrix

# build the heavy image once, then reuse it (matrix does this automatically)
make e2e-platform-build
E2E_NO_BUILD=1 tests/e2e/platform/run-e2e.sh env/dns-doh.env

# debug: leave the stack up after the run
KEEP_UP=1 tests/e2e/platform/run-e2e.sh env/dns-blocked.env
```

Requirements: Docker + Compose v2, and `uv` (for the pytest runner). The runner
builds the Shelfmark image from the repo `Dockerfile`, boots the profile's stack,
waits for `/api/health`, runs the suite, and tears down. `run-matrix.sh` builds the
image **once** and reuses it across profiles (`E2E_NO_BUILD=1`) so the slow
xvfb/chromium layer isn't rebuilt per profile.

## How profiles work

Each profile is an env file in `env/`. It sets:
- `COMPOSE_PROFILES` — which optional services start (compose `profiles:`).
- `SM_*` — the app's config, injected as container env. Shelfmark treats
  deployment ENV as authoritative (`config.get`: "Deployment-level ENV values
  always win"), so a profile fully determines the app's DNS/proxy/bypasser/source
  configuration with no runtime mutation.
- `E2E_PROFILE` — handed to pytest so the suite selects applicable tests.

Tests declare applicability with `@pytest.mark.profiles(...)`. **A test with no
marker is a profile-agnostic invariant and runs under every profile** — that is
how one cluster test ("source must be reachable") becomes the config matrix.

## The matrix (cluster × profile)

Status column: ✅ = run live on Docker and passing. Every profile below was run
end-to-end (`docker compose up` + suite + teardown) and passes.

| Profile | Egress / what it proves | Clusters | Regression targets | Status |
|---|---|---|---|---|
| `baseline` | Direct to fake AA; search/parse + #1028 clean-failure | 2,3,4 | #198 #293 #214 #1040 #1028 | ✅ 9 passed |
| `bypasser-external` | External bypasser wired; CF-gated search fails cleanly | 1 | #284 #202 #410 #369 | ✅ 5 passed |
| `bypasser-disabled` | CF-gated AA + bypasser OFF → no results (control) | 1 | #202 #410 | ✅ 4 passed |
| `dns-manual` | AA only resolvable via custom DNS (coredns) | config: DNS | #108 | ✅ 4 passed |
| `dns-blocked` | System DNS NXDOMAINs AA; custom DNS resolves it | config: DNS | **#1028** | ✅ 4 passed |
| `dns-doh` | System DNS blocks AA; **DoH over real HTTPS** resolves it | config: DoH | **#1028** #108 | ✅ 3 passed |
| `proxy-http` | All egress via tinyproxy, **proven by proxy logs** | config: proxy | **#956** | ✅ 6 passed |
| `proxy-socks` | All egress via SOCKS5 (microsocks), traversal-checked | config: proxy | #956 | ✅ 5 passed |
| `tor` | `USING_TOR=true` boots clean (restarts=0) | 1/6 Tor boot | #1021 #940 #801 | ✅ 5 passed |
| `client-transmission` | Prowlarr → **real Transmission** webseed download → /books | 5 clients | #1022 #634 | ✅ 4 passed |
| `client-deluge` | Prowlarr → **real Deluge** webseed download → /books | 5 clients | #530 | ✅ 4 passed |
| `full` | **real Chrome solves Cloudflare** + DoH + **real qBittorrent** download → /books (Moby-Dick) | 1,4,5 + DoH | **#284 #1030** #386 #1040 #214 | ✅ 6 passed |
| *(every profile)* | boots healthy under PUID/PGID, no perm errors | 6 entrypoint | #171 #447 #801 | ✅ |

> **The bypasser now runs for search too.** AA search/detail used to be fetched with
> `allow_bypasser_fallback=False`, so a search behind Cloudflare returned 503
> regardless of the bypasser — which left search dead when AA put DDoS-Guard in front
> of `/search`. Both now pass `allow_bypasser_fallback=True`, so a 403 switches
> straight to the bypasser rather than rotating to another mirror behind the same
> gate. The bypasser profiles therefore assert that a gated search *recovers* when the
> external bypasser is available and still fails cleanly when it is off, while the
> **`full` profile exercises the real end-to-end
> CF solve**: the gate is the AA mirror but challenges only the slow-download page,
> so downloading Moby-Dick forces the in-image headless Chromium to
> detect the challenge, solve it (`_bypass_method_cdp_gui_click`), and fetch the file —
> verified live (`Challenge detected: cloudflare` → `Bypass successful` → Moby-Dick
> in `/books`).
>
> **`bypasser-external` must set `SM_USING_EXTERNAL_BYPASSER=true`** — shelfmark does
> **not** derive it from `EXT_BYPASSER_URL`; without it the app silently uses the
> in-image Chrome bypasser instead of FlareSolverr.

Coverage of the 7 clusters from the analysis:

1. **Bypasser/Tor/Cloudflare** → `bypasser-external`, `bypasser-disabled`, `tor`.
2. **Search/metadata** → `baseline` (`test_cluster_search_aa.py`, hermetic via the
   `direct_download` source so no external metadata provider is needed).
3. **AA parsing/mirrors** → `baseline` parse guards incl. the **layout-drift
   fail-loud** test (#878/#879/#880).
4. **Permissions/file-move** → `baseline` (`test_cluster_download_permissions.py`).
5. **Torrent/usenet clients** → a mock Prowlarr + webseed torrent drives **three
   real torrent clients** end to end (`full`=qBittorrent, `client-transmission`,
   `client-deluge`) — completion detection + file move into `/books`. One
   client-agnostic test (`test_cluster_clients.py`) covers all three.
6. **Docker/entrypoint/PUID-PGID** → profile-agnostic health + boot-log checks,
   run under every profile.
7. **Audiobook/ABB** → parse-contract guards in
   `tests/audiobookbay/test_scraper_contract.py` (info-hash normalization #386,
   magnet fallback, layout drift). These run in **normal CI**, not the docker
   matrix, because ABB hardcodes `https://` for its fetches (see Roadmap).

### Proxy traversal (not just reachability)
Because the app and the mock AA share the e2e network, a regression that ignores
the proxy config would still reach AA directly. `test_egress_actually_traverses_proxy`
drives a search and then inspects the proxy container's logs, so the proxy
profiles prove the egress *went through* the proxy — a real guard for #956.

### DoH — two layers
- **Offline** (`tests/download/test_doh_resolver_mock.py`, normal CI): the real
  `DoHResolver` is driven against the mock `doh` role over localhost HTTP, covering
  JSON-answer parsing, NXDOMAIN → empty, and caching.
- **In-stack** (`dns-doh` profile): the mock `doh` role serves the DNS JSON API over
  **real HTTPS** (self-signed). The system resolver (coredns-blocked) NXDOMAINs
  `aa.mock.test`, so the host can *only* be resolved via DoH; compose `extra_hosts`
  redirects the `cloudflare-dns.com` provider to the in-stack mock and
  `CERTIFICATE_VALIDATION=disabled` accepts the self-signed cert. The search reaching
  AA proves the app's DoH path resolved the name end to end — **no app code change**.

### The `full` profile — real Chrome + real client (`make e2e-platform-full`)
The "everything real" heavy profile (test book: **Moby-Dick**), run nightly / on
demand (excluded from the PR matrix). It spins up, with **no** mock bypasser, and
**passes live** (6 passed):

- **Real Chrome solves Cloudflare, end to end (VERIFIED).** The AA mirror is the
  Cloudflare gate (`mock-cf`, `CF_GATED_PATHS=/slow_download/`): it passes
  search/detail and the final file through to `mock-aa` but challenges the AA
  *slow-download* page, whose challenge runs JS that issues `cf_clearance` and
  reloads. The gate has to be the only AA mirror: shelfmark refuses unconfigured
  hosts that resolve to private addresses, and with a second mirror the mirror
  selector would rewrite the gated link onto the ungated host. Downloading Moby-Dick forces the in-image headless Chromium (seleniumbase
  CDP, in the `shelfmark` image via `xvfb`+`chromium`) to load the gate, detect the
  challenge (`Challenge detected: cloudflare`), solve it (`_bypass_method_cdp_gui_click`),
  and fetch the cleared "Download now" page → the file lands in `/books`. That
  outcome is *only* reachable if Chrome solved the gate — the literal "spin a Chrome
  browser" path and the strongest guard for the #1 cluster. Two subtleties this
  surfaced, now handled by the mock: the cleared page must exceed the bypasser's
  `_LOADING_BODY_LENGTH_MAX` (50 chars of innerText) or it loops as "still loading",
  and the AA detail page must satisfy the brittle `original_nodes[-6]` parse (#880).
- **DoH** on at boot.
- **Real qBittorrent download.** A mock Prowlarr (`/api/v1/system/status`,
  `/api/v1/indexer`, torznab search) returns one release whose `.torrent` is a
  **tracker-less BEP-19 webseed** pointing at `mock-aa`'s HTTP payload. A real
  qBittorrent completes the download over HTTP (no tracker/peer/seeder), and
  shelfmark's completion detection + file move lands the book in `/books`. The
  webseed torrent is generated by `mocks/make_webseed_torrent.py` (cross-checked
  against shelfmark's own bencode/infohash parser in
  `tests/download/test_webseed_torrent_generator.py`), and the whole
  prowlarr→qBittorrent path is configured declaratively via env (`env/full.env`).

## Components

| Path | Purpose |
|---|---|
| `mocks/mock_services.py` | One Flask app, five roles (`origin-aa`, `cloudflare`, `flaresolverr`, `prowlarr`, `doh`) selected by `MOCK_ROLE`. `origin-aa` also serves the webseed payload + `.torrent`. |
| `mocks/make_webseed_torrent.py` | Stdlib bencode + BEP-19 webseed `.torrent` generator for the `full` real-client download. |
| `qbittorrent/qBittorrent.conf` | Real qBittorrent config (auth bypassed for the e2e subnet) for the `full` profile. |
| `env/full.env` | The heavy `full` profile: real Chrome bypasser + DoH + real qBittorrent. |
| `mocks/fixtures/*.html` | AA search/detail HTML in the **exact** shape the parser expects, plus drift/empty/no-files variants. |
| `docker-compose.e2e.yml` | The stack; optional services gated by compose profiles, static IPs for DNS determinism. |
| `dns/Corefile*`, `dns/mock.test.db` | coredns zones — working + ISP-block (NXDOMAIN). |
| `env/*.env` | The config profiles (matrix rows). |
| `suite/` | The pytest harness + cluster tests. |
| `run-e2e.sh` / `run-matrix.sh` | Boot one profile / loop the matrix. |
| `build-images.sh` | Build the heavy image once (`make e2e-platform-build`); reused via `E2E_NO_BUILD=1`. |

### Fault injection
The mock AA reproduces historical bugs deterministically. Injection rides inside
the search query as `E2EINJECT:<name>` (the app builds the AA URL itself and only
forwards the user query as `q=`). Names: `no_files`, `empty`, `layout_drift`,
`500`. The harness embeds them via `PlatformClient.direct_search(..., inject=...)`.

## Gating PRs (block merge on e2e failure)

The `.github/workflows/e2e-platform.yml` workflow runs on every PR. On a PR that
touches relevant code (`shelfmark/**`, `Dockerfile`, `entrypoint.sh`, `tor.sh`,
`tests/e2e/platform/**`) it runs the fast PR subset **and** the heavy `full`
profile (real Chrome solving Cloudflare + DoH + real qBittorrent), then a single
**`e2e required`** job aggregates them: it fails if any e2e job failed, and passes
(so it never hangs) when the e2e jobs are skipped on an unrelated PR.

The workflow producing a failing check is **not enough on its own** — GitHub only
*blocks merge* on checks listed in branch protection. A repo **admin** must, once:

- **UI:** Settings → Branches → branch protection rule for `main` →
  *Require status checks to pass before merging* → add **`e2e required`**.
- **or `gh` (admin token):**
  ```bash
  gh api -X PUT repos/calibrain/shelfmark/branches/main/protection \
    -H "Accept: application/vnd.github+json" --input - <<'JSON'
  { "required_status_checks": { "strict": true, "contexts": ["e2e required"] },
    "enforce_admins": true, "required_pull_request_reviews": null, "restrictions": null }
  JSON
  ```

After that, any failure in the e2e platform tests (including the `full` profile)
blocks the PR from merging. Requiring just the one `e2e required` context covers
the whole dynamic matrix, so the list never needs updating as profiles change.

## Known limitations / follow-ups

- **rTorrent.** Not in the matrix: its rakshasa-libtorrent has **no GetRight/webseed
  support**, so the hermetic webseed torrent (which qBittorrent/Transmission/Deluge
  all complete) leaves rTorrent stuck at 0%. Supporting it needs a real tracker +
  seeder (peer download) — a follow-up that the webseed design intentionally avoids.
- **Usenet clients (SABnzbd/NZBGet).** Not yet covered — completing a usenet download
  hermetically needs a mock NNTP server serving the yEnc-encoded payload plus an NZB,
  which is a separate (larger) build than the torrent webseed path.
- **Audiobook (cluster 7) in-stack.** ABB hardcodes `https://`, so it's covered
  offline (`tests/audiobookbay/test_scraper_contract.py`); an in-stack
  `audiobookbay` role needs the same self-signed-HTTPS plumbing the `dns-doh` profile
  now uses for DoH.
