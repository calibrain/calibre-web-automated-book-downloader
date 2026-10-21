"""A rendered page carrying Cloudflare's bot-detection beacon is not a challenge.

Cloudflare injects an iframe loading /cdn-cgi/challenge-platform/scripts/jsd/main.js into
every page a protected site serves. The raw HTML has no such reference; the browser adds
it after load. FlareSolverr returns the rendered DOM, so every successful OceanofPDF solve
came back carrying "/cdn-cgi/challenge-platform" and was rejected as an unsolved
challenge - 26 KB results pages, retried five times each, then reported as unreachable.
"""

from shelfmark.bypass.challenge import challenge_marker

# Shape of a rendered OceanofPDF search page: the site's own markup plus the beacon.
RENDERED_PAGE_WITH_BEACON = (
    "<html><head><title>You searched for dune - OceanofPDF</title></head><body>"
    '<a href="#genesis-content">Skip to main content</a>'
    '<main id="genesis-content"><article aria-label="[PDF] [EPUB] Dune Download">'
    '<h2 class="entry-title"><a href="/authors/frank-herbert/dune/">Dune</a></h2>'
    "</article></main>"
    "<script>(function(){var a=document.createElement('script');"
    "a.src='/cdn-cgi/challenge-platform/scripts/jsd/main.js';"
    "document.getElementsByTagName('head')[0].appendChild(a);})();</script>"
    '<iframe height="1" width="1" style="visibility:hidden"></iframe>'
    "</body></html>"
)

# Cloudflare's managed challenge, trimmed to its markers. It can carry the beacon too.
MANAGED_CHALLENGE = (
    "<html><head><title>Just a moment...</title></head><body>"
    "<script>window._cf_chl_opt={cType:'managed'};</script>"
    '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>'
    '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
    "</body></html>"
)


def test_beacon_on_a_rendered_page_is_not_a_challenge():
    assert challenge_marker(RENDERED_PAGE_WITH_BEACON) is None


def test_beacon_path_in_any_case_is_ignored():
    assert challenge_marker(RENDERED_PAGE_WITH_BEACON.replace("scripts/jsd", "Scripts/JSD")) is None


def test_managed_challenge_is_still_caught_with_the_beacon_present():
    assert challenge_marker(MANAGED_CHALLENGE) is not None


def test_challenge_script_path_alone_is_still_caught():
    page = '<html><script src="/cdn-cgi/challenge-platform/h/g/orchestrate/x"></script></html>'

    assert challenge_marker(page) == "/cdn-cgi/challenge-platform"
