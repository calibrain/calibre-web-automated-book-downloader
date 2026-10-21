"""Controllable mock services for the Shelfmark e2e Docker platform.

A single Flask app that plays one of several *roles*, selected by the
``MOCK_ROLE`` environment variable. Running one image with different roles keeps
the platform image small and the behaviour in one auditable place.

Roles
-----
``origin-aa``     Fake Anna's Archive: search results table, ``/md5/<id>`` detail
                  pages, and a downloadable book file. The HTML mirrors the real
                  selectors the parser depends on (``<tr>`` rows, last-cell
                  distant path, ``get.php?md5=..&key=..`` GET links) so parser
                  drift (#878/#879/#880) is caught here. Supports fault injection
                  via query flags to reproduce historical bugs deterministically.
``cloudflare``    Cloudflare-protected origin: returns a 403 "Just a moment..."
                  challenge page (with ``cf-mitigated: challenge``) until the
                  request carries a ``cf_clearance`` cookie, then serves the real
                  content. ``CF_GATED_PATHS`` limits the challenge to some paths.
                  Exercises the app's CF detection + bypasser routing
                  (#284, #226, #202, #1030) without running real CF JS.
``flaresolverr``  Mock FlareSolverr implementing the ``/v1`` contract. It fetches
                  the requested URL *with* a clearance cookie and returns the
                  solved HTML + cookies, so ``_fetch_via_bypasser`` runs end to
                  end deterministically (no headless Chrome needed in CI).
``doh``           Minimal DNS-over-HTTPS (RFC 8484 + Google JSON) responder used
                  to verify the USE_DOH path resolves mock domains even when the
                  system resolver is poisoned (#1028).
``all``           Mounts every role at once (default; handy for local poking).

Fault injection (origin-aa) rides INSIDE the search query as an
``E2EINJECT:<name>`` token, because the app builds the AA URL itself and only
forwards the user query as ``q=``. The harness embeds it (see
PlatformClient.direct_search); the mock strips it before rendering:
  ``no_files``      -> renders the literal "No files found." alongside a real row
                       (regression for the false-positive check).
  ``empty``         -> renders an empty results page (true "No files found").
  ``layout_drift``  -> renders a structurally-changed page (cards, no <table>) so
                       a hardcoded-index parser yields zero rows — the app must
                       fail loudly, not silently (#878/#879/#880).
  ``500``           -> returns HTTP 500 (mirror failover path).
"""

from __future__ import annotations

import base64
import os
import re
import struct
from pathlib import Path

from flask import Flask, Response, jsonify, make_response, request

FIXTURES = Path(__file__).parent / "fixtures"
ROLE = os.environ.get("MOCK_ROLE", "all").strip().lower()
# Hostname the flaresolverr/cloudflare roles use to reach the AA origin from
# inside the compose network.
ORIGIN_INTERNAL_URL = os.environ.get("ORIGIN_INTERNAL_URL", "http://mock-aa")
CLEARANCE_COOKIE = "cf_clearance"
CLEARANCE_VALUE = "e2e-cleared-token"

# Test book: *Moby-Dick* by Herman Melville (public domain), used both as the
# webseed-torrent payload (qBittorrent path) and the AA slow-download payload (real
# Chrome / Cloudflare path). PAYLOAD_NAME/URL must stay in sync between what mock-aa
# serves and what the torrent's url-list references.
PAYLOAD_NAME = "moby-dick.epub"
PAYLOAD_URL = f"{os.environ.get('PAYLOAD_PUBLIC_URL', 'http://mock-aa')}/payload/{PAYLOAD_NAME}"

# Comma-separated path prefixes the cloudflare role challenges. Empty -> every path.
# The `full` profile gates only /slow_download/, so search/detail pass straight through
# and the *download* is what makes the internal Chrome bypasser solve the challenge.
CF_GATED_PATHS = tuple(
    prefix.strip() for prefix in os.environ.get("CF_GATED_PATHS", "").split(",") if prefix.strip()
)

# Real public-domain opening of Moby-Dick (Chapter 1, "Loomings").
_MOBY_DICK_TEXT = (
    "Call me Ishmael. Some years ago—never mind how long precisely—having little "
    "or no money in my purse, and nothing particular to interest me on shore, I "
    "thought I would sail about a little and see the watery part of the world. It "
    "is a way I have of driving off the spleen and regulating the circulation. "
    "Whenever I find myself growing grim about the mouth; whenever it is a damp, "
    "drizzly November in my soul; whenever I find myself involuntarily pausing "
    "before coffin warehouses, and bringing up the rear of every funeral I meet; "
    "and especially whenever my hypos get such an upper hand of me, that it "
    "requires a strong moral principle to prevent me from deliberately stepping "
    "into the street, and methodically knocking people's hats off—then, I account "
    "it high time to get to sea as soon as I can."
)

app = Flask(__name__)


def _payload_bytes() -> bytes:
    """Deterministic *Moby-Dick* EPUB used as the download payload.

    Determinism matters: the webseed torrent's piece hashes are computed from these
    exact bytes, so any drift between what mock-aa serves and what the torrent
    describes would make qBittorrent never complete. Fixed ZipInfo timestamps keep
    the bytes byte-stable across runs.
    """
    import io
    import zipfile

    files = [
        ("mimetype", "application/epub+zip"),
        (
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        ),
        (
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
            'version="3.0" unique-identifier="id"><metadata '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            '<dc:identifier id="id">e2e-moby-dick</dc:identifier>'
            "<dc:title>Moby-Dick; or, The Whale</dc:title>"
            "<dc:creator>Herman Melville</dc:creator>"
            "<dc:language>en</dc:language></metadata>"
            '<manifest><item id="c1" href="chapter1.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c1"/></spine></package>',
        ),
        (
            "OEBPS/chapter1.xhtml",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Loomings</title>'
            "</head><body><h1>Chapter 1. Loomings.</h1>"
            # Repeat the opening so the EPUB clears shelfmark's 10 KB minimum-size
            # check on the direct-download path (_MIN_VALID_FILE_SIZE).
            + ("<p>" + _MOBY_DICK_TEXT + "</p>") * 24
            + "</body></html>",
        ),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, content in files:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zf.writestr(info, content)
    return buf.getvalue()


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Role: origin-aa  (fake Anna's Archive)
# --------------------------------------------------------------------------- #
def _search_rows() -> str:
    """One real result row in the exact shape the parser expects."""
    return _read_fixture("aa_search_results.html")


def register_origin_aa(flask_app: Flask) -> None:
    @flask_app.route("/search")
    def aa_search() -> Response:
        query = request.args.get("q", "")
        # Fault injection travels INSIDE the search query (the app builds the AA
        # URL itself and won't forward arbitrary params), via a token the harness
        # embeds: "E2EINJECT:<name> <real query>".
        inject = ""
        match = re.search(r"E2EINJECT:(\w+)", query)
        if match:
            inject = match.group(1)
            query = re.sub(r"E2EINJECT:\w+\s*", "", query).strip()
        if inject == "500":
            return make_response("upstream error", 500)
        if inject == "empty":
            body = _read_fixture("aa_search_empty.html")
            return make_response(body, 200)
        if inject == "no_files":
            # Real row present *and* the "No files found." string — the historical
            # false-positive (#: 'No files found' check). The app must still
            # surface the real row.
            body = _search_rows().replace("<!-- NO_FILES_MARKER -->", "<div>No files found.</div>")
            return make_response(body, 200)
        if inject == "layout_drift":
            return make_response(_read_fixture("aa_search_layout_drift.html"), 200)
        # Echo the query into the title so tests can assert routing worked.
        body = _search_rows().replace("__QUERY__", query or "A Book Title")
        return make_response(body, 200)

    @flask_app.route("/md5/<book_id>")
    def aa_detail(book_id: str) -> Response:
        body = _read_fixture("aa_detail.html").replace("__MD5__", book_id)
        return make_response(body, 200)

    @flask_app.route("/get.php")
    def aa_getphp() -> Response:
        # The actual file download link target (get.php?md5=..&key=..).
        return _serve_book()

    @flask_app.route("/slow_download/<path:rest>")
    def aa_slow(rest: str) -> Response:
        # The AA "slow partner server" page: an HTML page whose "Download now" link
        # is the final file URL. shelfmark's _extract_slow_download_url parses this.
        # In the full profile this sits behind the Cloudflare gate, so it is reached
        # only after the internal Chrome bypasser solves the challenge.
        del rest
        file_url = f"{os.environ.get('AA_FILE_BASE', 'http://mock-aa')}/file/{PAYLOAD_NAME}"
        # The visible text must exceed the internal bypasser's "still loading"
        # threshold (_LOADING_BODY_LENGTH_MAX = 50 chars of body.innerText) or it
        # never considers the (cleared) page settled and loops until timeout.
        html = (
            "<!doctype html><html><head><title>Download</title></head><body>"
            "<h1>Anna&rsquo;s Archive &mdash; Slow Partner Server</h1>"
            "<p>Your download of <em>Moby-Dick; or, The Whale</em> by Herman Melville "
            "is ready. Use the link below to download the file from this slow partner "
            "server. The connection is slow but free, with no waitlist.</p>"
            "<div class='top-row'>"
            f'<a href="{file_url}" download>\U0001f4da Download now</a>'
            "</div>"
            "<p>Thank you for supporting open access to knowledge.</p>"
            "</body></html>"
        )
        return make_response(html, 200)

    @flask_app.route(f"/file/{PAYLOAD_NAME}")
    def aa_file() -> Response:
        # Final file URL extracted from the slow-download page.
        return _serve_book()

    @flask_app.route("/dyn/api/fast_download.json")
    def aa_fast() -> Response:
        md5 = request.args.get("md5", "")
        return jsonify({"download_url": f"{request.host_url.rstrip('/')}/get.php?md5={md5}&key=k"})

    # --- webseed payload + torrent for the `full` real-client pipeline --------
    @flask_app.route(f"/payload/{PAYLOAD_NAME}")
    def aa_payload() -> Response:
        # Range support is required for transmission's GetRight webseed (it fetches
        # pieces with `Range: bytes=...` and expects 206); libtorrent clients
        # (qBittorrent/deluge) tolerate a plain 200, but transmission does not.
        return _ranged_response(_payload_bytes(), "application/epub+zip")

    @flask_app.route("/payload.torrent")
    def aa_torrent() -> Response:
        torrent = _build_payload_torrent()
        resp = make_response(torrent)
        resp.headers["Content-Type"] = "application/x-bittorrent"
        resp.headers["Content-Disposition"] = 'attachment; filename="sample-book.torrent"'
        return resp


def _ranged_response(data: bytes, content_type: str) -> Response:
    """Serve ``data`` honoring a single HTTP Range request (206 + Content-Range).

    Needed so transmission's webseed (which fetches via ``Range: bytes=...``) can
    download piece by piece. A request without Range gets the full 200 body.
    """
    total = len(data)
    range_header = request.headers.get("Range", "")
    if range_header.startswith("bytes="):
        first = range_header[len("bytes=") :].split(",", 1)[0]
        start_s, _, end_s = first.partition("-")
        try:
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else total - 1
        except ValueError:
            start, end = 0, total - 1
        end = min(end, total - 1)
        start = max(0, min(start, end))
        chunk = data[start : end + 1]
        resp = make_response(chunk, 206)
        resp.headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    else:
        resp = make_response(data)
    resp.headers["Content-Type"] = content_type
    resp.headers["Accept-Ranges"] = "bytes"
    return resp


def _serve_book() -> Response:
    resp = _ranged_response(_payload_bytes(), "application/epub+zip")
    resp.headers["Content-Disposition"] = f'attachment; filename="{PAYLOAD_NAME}"'
    return resp


def _build_payload_torrent() -> bytes:
    """Webseed .torrent for the deterministic payload, sourced only from mock-aa.

    Imported lazily so the doh/cloudflare/flaresolverr roles don't need the
    generator module on the path.
    """
    import importlib.util

    gen_path = Path(__file__).parent / "make_webseed_torrent.py"
    spec = importlib.util.spec_from_file_location("make_webseed_torrent", gen_path)
    assert spec and spec.loader
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    return gen.build_webseed_torrent(PAYLOAD_NAME, _payload_bytes(), PAYLOAD_URL)


# --------------------------------------------------------------------------- #
# Role: cloudflare  (challenge until clearance cookie present)
# --------------------------------------------------------------------------- #
def register_cloudflare(flask_app: Flask) -> None:
    @flask_app.route("/", defaults={"path": ""})
    @flask_app.route("/<path:path>")
    def cf_gate(path: str) -> Response:
        gated = not CF_GATED_PATHS or request.path.startswith(CF_GATED_PATHS)
        if not gated or request.cookies.get(CLEARANCE_COOKIE) == CLEARANCE_VALUE:
            # Ungated or cleared: proxy the request through to the real AA origin.
            return _cleared_passthrough(path)
        challenge = _read_fixture("cf_challenge.html")
        resp = make_response(challenge, 403)
        resp.headers["cf-mitigated"] = "challenge"
        resp.headers["Server"] = "cloudflare"
        return resp


def _cleared_passthrough(path: str) -> Response:
    import requests as _rq

    target = f"{ORIGIN_INTERNAL_URL}/{path}"
    upstream = _rq.get(target, params=request.args, timeout=10)
    resp = make_response(upstream.content, upstream.status_code)
    resp.headers["Content-Type"] = upstream.headers.get("Content-Type", "text/html")
    return resp


# --------------------------------------------------------------------------- #
# Role: flaresolverr  (mock external bypasser, /v1 contract)
# --------------------------------------------------------------------------- #
def register_flaresolverr(flask_app: Flask) -> None:
    @flask_app.route("/v1", methods=["POST"])
    def v1() -> Response:
        import requests as _rq

        payload = request.get_json(silent=True) or {}
        url = payload.get("url", "")
        if not url:
            return jsonify({"status": "error", "message": "missing url"}), 400
        # "Solve" the challenge by fetching with the clearance cookie set.
        upstream = _rq.get(url, cookies={CLEARANCE_COOKIE: CLEARANCE_VALUE}, timeout=15)
        return jsonify(
            {
                "status": "ok",
                "message": "Challenge solved!",
                "solution": {
                    "url": url,
                    "status": upstream.status_code,
                    "response": upstream.text,
                    "cookies": [{"name": CLEARANCE_COOKIE, "value": CLEARANCE_VALUE, "domain": ""}],
                    "userAgent": "Mozilla/5.0 (e2e-flaresolverr)",
                },
            }
        )


# --------------------------------------------------------------------------- #
# Role: prowlarr  (minimal Prowlarr API for the `full` real-client pipeline)
# --------------------------------------------------------------------------- #
# Implements just the endpoints shelfmark's prowlarr client calls, returning one
# torrent release whose download is mock-aa's webseed .torrent. A real
# qBittorrent then completes the download over HTTP (no tracker/peer needed).
AA_INTERNAL_URL = os.environ.get("AA_INTERNAL_URL", "http://mock-aa")


def register_prowlarr(flask_app: Flask) -> None:
    @flask_app.route("/api/v1/system/status")
    def prowlarr_status() -> Response:
        return jsonify({"appName": "Prowlarr", "version": "1.30.0.4000", "instanceName": "e2e"})

    @flask_app.route("/api/v1/indexer")
    def prowlarr_indexers() -> Response:
        return jsonify(
            [
                {
                    "id": 1,
                    "name": "Mock Torznab",
                    "enable": True,
                    "protocol": "torrent",
                    "implementation": "Torznab",
                    "implementationName": "Generic Torznab",
                    "definitionName": "mock-torznab",
                    "capabilities": {
                        "categories": [
                            {"id": 7000, "name": "Books"},
                            {"id": 7020, "name": "Books/EBook"},
                        ]
                    },
                }
            ]
        )

    @flask_app.route("/api/v1/indexer/<int:indexer_id>/newznab")
    def prowlarr_torznab(indexer_id: int) -> Response:
        del indexer_id
        t = request.args.get("t", "search")
        if t == "caps":
            return Response(_torznab_caps(), mimetype="application/xml")
        query = request.args.get("q", "") or "E2E Mock Book"
        return Response(_torznab_search(query), mimetype="application/xml")


def _torznab_caps() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<caps><server title="Mock Torznab"/>'
        '<limits max="100" default="50"/>'
        '<searching><search available="yes" supportedParams="q"/>'
        '<book-search available="yes" supportedParams="q,author,title"/></searching>'
        '<categories><category id="7000" name="Books">'
        '<subcat id="7020" name="EBook"/></category></categories></caps>'
    )


def _torznab_search(query: str) -> str:
    torrent_url = f"{AA_INTERNAL_URL}/payload.torrent"
    size = len(_payload_bytes())
    title = f"{query} - E2E Mock Book"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">'
        "<channel>"
        "<item>"
        f"<title>{title}</title>"
        "<guid>e2e-mock-release-1</guid>"
        f"<link>{torrent_url}</link>"
        f"<size>{size}</size>"
        "<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>"
        f'<enclosure url="{torrent_url}" length="{size}" type="application/x-bittorrent"/>'
        '<torznab:attr name="category" value="7020"/>'
        '<torznab:attr name="seeders" value="10"/>'
        '<torznab:attr name="peers" value="11"/>'
        '<torznab:attr name="downloadvolumefactor" value="0"/>'
        '<torznab:attr name="uploadvolumefactor" value="1"/>'
        "</item>"
        "</channel></rss>"
    )


# --------------------------------------------------------------------------- #
# Role: doh  (DNS over HTTPS responder)
# --------------------------------------------------------------------------- #
# Maps mock hostnames to the in-network IP the test wants them resolved to.
# Provided via DOH_MAP env: "host=ip,host2=ip2".
def _doh_map() -> dict[str, str]:
    raw = os.environ.get("DOH_MAP", "")
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            host, ip = pair.split("=", 1)
            out[host.strip().rstrip(".").lower()] = ip.strip()
    return out


def _encode_a_answer(name: str, ip: str) -> bytes:
    parts = ip.split(".")
    return struct.pack("!HHHIH4B", 0xC00C, 1, 1, 60, 4, *(int(p) for p in parts))


def register_doh(flask_app: Flask) -> None:
    @flask_app.route("/dns-query", methods=["GET", "POST"])
    @flask_app.route("/resolve", methods=["GET", "POST"])  # Google-style JSON endpoint
    def dns_query() -> Response:
        mapping = _doh_map()
        # Google/Cloudflare JSON form (?name=&type=A)
        name = (request.args.get("name") or "").rstrip(".").lower()
        if name:
            ip = mapping.get(name)
            answer = [{"name": name, "type": 1, "TTL": 60, "data": ip}] if ip else []
            return jsonify({"Status": 0 if ip else 3, "Answer": answer})
        # RFC 8484 wireformat (POST body or ?dns=)
        if request.method == "POST":
            wire = request.get_data()
        else:
            dns_b64 = request.args.get("dns", "")
            wire = base64.urlsafe_b64decode(dns_b64 + "=" * (-len(dns_b64) % 4))
        return _wireformat_response(wire, mapping)

    def _wireformat_response(wire: bytes, mapping: dict[str, str]) -> Response:
        # Minimal parser: echo header/question, append one A answer if known.
        txid = wire[0:2]
        qname, _ = _parse_qname(wire, 12)
        question = wire[12:]
        host = qname.rstrip(".").lower()
        ip = mapping.get(host)
        ancount = 1 if ip else 0
        header = txid + struct.pack("!HHHHH", 0x8180, 1, ancount, 0, 0)
        body = question + (_encode_a_answer(host, ip) if ip else b"")
        resp = make_response(header + body)
        resp.headers["Content-Type"] = "application/dns-message"
        return resp

    def _parse_qname(wire: bytes, offset: int) -> tuple[str, int]:
        labels = []
        while True:
            length = wire[offset]
            offset += 1
            if length == 0:
                break
            labels.append(wire[offset : offset + length].decode("ascii", "ignore"))
            offset += length
        return ".".join(labels), offset


# --------------------------------------------------------------------------- #
# Health + role wiring
# --------------------------------------------------------------------------- #
@app.route("/healthz")
def healthz() -> Response:
    return jsonify({"role": ROLE, "ok": True})


_ROLES = {
    "origin-aa": register_origin_aa,
    "cloudflare": register_cloudflare,
    "flaresolverr": register_flaresolverr,
    "prowlarr": register_prowlarr,
    "doh": register_doh,
}

if ROLE == "all":
    for _register in _ROLES.values():
        _register(app)
elif ROLE in _ROLES:
    _ROLES[ROLE](app)
else:  # pragma: no cover - misconfiguration guard
    raise SystemExit(f"Unknown MOCK_ROLE={ROLE!r}; expected one of {[*sorted(_ROLES), 'all']}")


def _self_signed_cert() -> tuple[str, str]:
    """Write a throwaway self-signed cert/key to /tmp and return their paths.

    Used only by the `doh` role's HTTPS server. The app reaches it with
    CERTIFICATE_VALIDATION=disabled, so the cert's identity is irrelevant.
    """
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "e2e-doh")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("cloudflare-dns.com")]), False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = "/tmp/doh.crt", "/tmp/doh.key"
    Path(cert_path).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    Path(key_path).write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


if __name__ == "__main__":
    if os.environ.get("DOH_TLS") == "1":
        cert_path, key_path = _self_signed_cert()
        app.run(host="0.0.0.0", port=443, ssl_context=(cert_path, key_path), threaded=True)
    else:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "80")))
