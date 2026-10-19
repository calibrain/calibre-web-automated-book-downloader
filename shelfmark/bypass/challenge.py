"""Challenge-page detection shared by the bypassers and the HTTP retry path.

Kept out of `internal_bypasser` so the HTTP layer can recognise an interstitial
without importing SeleniumBase: that module is imported lazily precisely because its
browser dependencies are optional, and external-bypasser setups run without them.
"""

# Matched against lowercased text, so every entry must be lowercase.
CLOUDFLARE_INDICATORS = [
    "just a moment",
    "verify you are human",
    "verifying you are human",
    "cloudflare.com/products/turnstile",
]

DDOS_GUARD_INDICATORS = [
    "ddos-guard",
    "ddos guard",
    "checking your browser before accessing",
    "complete the manual check to continue",
    "could not verify your browser automatically",
]

OTHER_CHALLENGE_INDICATORS = [
    "diamwall",
]

# Markers that exist only in raw markup: the bypassers scan rendered innerText, where
# a script src or a <title> never appears. The title match is scoped to the tag on
# purpose - hosts word the rest of that sentence differently, and matching "checking
# your browser" as free text would trip on any page that merely discusses a challenge.
_RAW_HTML_MARKERS = (
    "<title>checking your browser",
    "/cdn-cgi/challenge-platform",
    "/.well-known/ddos-guard/",
)

# Cloudflare's bot-detection beacon. Its script injects an iframe loading this path into
# every page a Cloudflare site serves, so a browser-rendered page - what FlareSolverr and
# the internal bypasser hand back - carries it after a *successful* solve. Real challenge
# pages load from /cdn-cgi/challenge-platform/h/... and keep their other markers, so
# discarding this one path before the scan does not hide them.
_CLOUDFLARE_BEACON_PATH = "/cdn-cgi/challenge-platform/scripts/jsd/"

# An interstitial is a few KB of markup. Past that it is a real page that happens to
# mention a marker - a protected site links its own DDoS-Guard endpoints on every page.
MAX_CHALLENGE_HTML_CHARS = 64 * 1024


def challenge_marker(html: str) -> str | None:
    """Return the marker proving `html` is an unsolved challenge page, or None.

    Only meaningful for a response that already carries a challenge status: the
    markers appear on protected sites' real pages too, so the status is what
    separates "blocked" from "served".
    """
    if not html or len(html) > MAX_CHALLENGE_HTML_CHARS:
        return None
    lowered = html.lower().replace(_CLOUDFLARE_BEACON_PATH, "")
    for marker in (
        *_RAW_HTML_MARKERS,
        *DDOS_GUARD_INDICATORS,
        *CLOUDFLARE_INDICATORS,
        *OTHER_CHALLENGE_INDICATORS,
    ):
        if marker in lowered:
            return marker
    return None
