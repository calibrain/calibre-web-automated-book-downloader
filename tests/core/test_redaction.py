"""The shared helpers that take credentials and links out of text."""

import pytest

from shelfmark.core import logger as logger_module
from shelfmark.core import redaction
from shelfmark.core.viewer_redaction import redact_status_message


class TestSafeUrl:
    def test_keeps_scheme_host_and_path_and_drops_the_query_and_fragment(self):
        url = "https://prowlarr.example:9696/1/download?apikey=s3cr3t&link=abc#frag"

        assert redaction.safe_url(url) == "https://prowlarr.example:9696/1/download"

    def test_is_cut_to_the_limit(self):
        assert len(redaction.safe_url("https://t.example/" + "a" * 500, limit=40)) == 40

    @pytest.mark.parametrize("value", ["magnet:?xt=urn:btih:abc", "not a url", ""])
    def test_a_value_that_is_not_a_url_is_not_echoed(self, value):
        assert redaction.safe_url(value) == "<unparsed url: str>"


class TestRedactUrlsInText:
    def test_every_url_loses_its_query(self):
        text = "GET https://a.example/x?apikey=k1 then https://b.example/y?token=k2 failed"

        assert redaction.redact_urls_in_text(text) == (
            "GET https://a.example/x then https://b.example/y failed"
        )

    def test_a_closing_bracket_or_quote_is_not_part_of_the_link(self):
        text = "failed (https://a.example/x?apikey=k1) and 'https://b.example/y?key=k2'"

        assert redaction.redact_urls_in_text(text) == (
            "failed (https://a.example/x) and 'https://b.example/y'"
        )

    def test_the_path_a_failed_request_prints_keeps_everything_before_its_query(self):
        text = (
            "Max retries exceeded with url: /api/v1/indexer/1/download?apikey=s3cr3t&link=abc (x)"
        )

        assert redaction.redact_urls_in_text(text) == (
            "Max retries exceeded with url: /api/v1/indexer/1/download (x)"
        )

    def test_text_without_a_link_is_untouched(self):
        assert redaction.redact_urls_in_text("Connection refused") == "Connection refused"


class TestStripLinks:
    def test_replaces_a_full_url(self):
        assert redaction.strip_links("401 for url: https://i.example/dl?apikey=k") == (
            "401 for url: (link removed)"
        )

    def test_replaces_the_scheme_less_path_after_url(self):
        assert redaction.strip_links("Max retries exceeded with url: /a/b?apikey=k (Caused)") == (
            "Max retries exceeded with url: (link removed) (Caused)"
        )

    def test_the_scheme_is_case_insensitive(self):
        assert redaction.strip_links("HTTPS://I.example/dl?k=1 failed") == "(link removed) failed"


def test_the_viewer_message_redaction_is_the_shared_one():
    message = "Unauthorized for url: https://i.example/dl?apikey=k"

    assert redact_status_message(message) == redaction.strip_links(message)


def test_the_log_formatter_masks_with_the_shared_function():
    assert logger_module.redact_secrets is redaction.redact_secrets
