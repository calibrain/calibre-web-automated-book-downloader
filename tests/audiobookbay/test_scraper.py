"""
Tests for AudiobookBay scraper functions.
"""

from unittest.mock import patch

from shelfmark.release_sources.audiobookbay import scraper

# Mock HTML based on real ABB structure
SAMPLE_SEARCH_HTML = """
<html>
<body>
<div class="post">
    <div class="postTitle"><h2><a href="/abss/test-book-title-by-author/" rel="bookmark">Test Book Title - Test Author</a></h2></div>
    <div class="postInfo">Category: Genre&nbsp; <br>Language: English<span style="margin-left:100px;">Keywords: Test Keywords&nbsp;</span><br></div>
    <div class="postContent">
        <div class="center">
            <p class="center">Shared by:<a href="/member/users/index?&mode=userinfo&username=testuser">testuser</a></p>
            <p class="center"><a href="https://audiobookbay.lu/abss/test-book-title-by-author/"><img src="https://example.com/cover.jpg" alt="Test Cover" width="250"></a></p>
        </div>
        <p style="text-align:center;">Posted: 01 Jan 2024<br>Format: <span style="color:#a00;">M4B</span> / Bitrate: <span style="color:#a00;">128 Kbps</span><br>File Size: <span style="color:#00f;">500.00</span> MBs</p>
    </div>
    <div class="postMeta">
        <span class="postLink"><a href="https://audiobookbay.lu/abss/test-book-title-by-author/">Audiobook Details</a></span>
        <span class="postComments"><a href="/dload-now?ll=test" rel="nofollow">Direct Download</a></span>
    </div>
</div>
<div class="post">
    <div class="postTitle"><h2><a href="/abss/another-test-book/" rel="bookmark">Another Test Book - Another Author</a></h2></div>
    <div class="postInfo">Category: Fiction&nbsp; <br>Language: Spanish<span style="margin-left:100px;">Keywords: Test&nbsp;</span><br></div>
    <div class="postContent">
        <div class="center">
            <p class="center">Shared by:<a href="/member/users/index?&mode=userinfo&username=user2">user2</a></p>
            <p class="center"><a href="https://audiobookbay.lu/abss/another-test-book/"><img src="https://example.com/cover2.jpg" alt="Cover 2" width="250"></a></p>
        </div>
        <p style="text-align:center;">Posted: 15 Nov 2023<br>Format: <span style="color:#a00;">MP3</span> / Bitrate: <span style="color:#a00;">256 Kbps</span><br>File Size: <span style="color:#00f;">1.01</span> GBs</p>
    </div>
    <div class="postMeta">
        <span class="postLink"><a href="https://audiobookbay.lu/abss/another-test-book/">Audiobook Details</a></span>
        <span class="postComments"><a href="/dload-now?ll=test2" rel="nofollow">Direct Download</a></span>
    </div>
</div>
</body>
</html>
"""

EMPTY_SEARCH_HTML = """
<html>
<body>
</body>
</html>
"""

# Mock HTML for detail page with info hash and trackers
SAMPLE_DETAIL_HTML = """
<html>
<body>
<table>
    <tr>
        <td>Info Hash</td>
        <td>ABC123DEF456789012345678901234567890ABCD</td>
    </tr>
    <tr>
        <td>Tracker 1</td>
        <td>udp://tracker.openbittorrent.com:80</td>
    </tr>
    <tr>
        <td>Tracker 2</td>
        <td>http://tracker.example.com:8080</td>
    </tr>
    <tr>
        <td>Other Info</td>
        <td>Some other data</td>
    </tr>
</table>
</body>
</html>
"""

DETAIL_HTML_NO_TRACKERS = """
<html>
<body>
<table>
    <tr>
        <td>Info Hash</td>
        <td>ABC123DEF456789012345678901234567890ABCD</td>
    </tr>
</table>
</body>
</html>
"""


class TestSearchAudiobookbay:
    """Tests for the search_audiobookbay function."""

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_success(self, mock_config_get, mock_html_get):
        """Test successful search with results."""
        mock_config_get.return_value = 1.0  # rate_limit_delay
        mock_html_get.return_value = (
            SAMPLE_SEARCH_HTML,
            "https://audiobookbay.lu/page/1/?s=test+query&cat=undefined%2Cundefined",
        )

        results = scraper.search_audiobookbay("test query", max_pages=1, hostname="audiobookbay.lu")

        assert len(results) == 2
        assert results[0]["title"] == "Test Book Title - Test Author"
        assert results[0]["link"] == "https://audiobookbay.lu/abss/test-book-title-by-author/"
        assert results[0]["language"] == "English"
        assert results[0]["format"] == "M4B"
        assert results[0]["bitrate"] == "128 Kbps"
        assert results[0]["size"] == "500.00 MB"
        assert results[0]["posted_date"] == "01 Jan 2024"
        assert results[0]["cover"] == "https://example.com/cover.jpg"

        assert results[1]["title"] == "Another Test Book - Another Author"
        assert results[1]["language"] == "Spanish"
        assert results[1]["format"] == "MP3"
        assert results[1]["size"] == "1.01 GB"

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_pagination(self, mock_config_get, mock_html_get):
        """Test pagination through multiple pages."""
        mock_config_get.return_value = 0.0  # No delay for faster tests
        mock_html_get.side_effect = [
            (EMPTY_SEARCH_HTML, "https://audiobookbay.lu/"),  # Session bootstrap
            (
                SAMPLE_SEARCH_HTML,
                "https://audiobookbay.lu/page/1/?s=test&cat=undefined%2Cundefined",
            ),
            (EMPTY_SEARCH_HTML, "https://audiobookbay.lu/page/2/?s=test&cat=undefined%2Cundefined"),
        ]

        results = scraper.search_audiobookbay("test", max_pages=2, hostname="audiobookbay.lu")

        assert len(results) == 2  # Only from first page
        assert mock_html_get.call_count == 3

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_page_one_uses_root_search_endpoint(
        self, mock_config_get, mock_html_get
    ):
        """Test page 1 search uses ABB root endpoint instead of /page/1/."""
        mock_config_get.return_value = 0.0
        mock_html_get.return_value = (
            SAMPLE_SEARCH_HTML,
            "https://audiobookbay.lu/?s=test",
        )

        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")

        assert len(results) == 2
        requested_url = mock_html_get.call_args.args[0]
        assert requested_url.startswith("https://audiobookbay.lu/?s=test")
        assert "/page/1/" not in requested_url
        assert "cat=undefined%2Cundefined" in requested_url

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_bootstraps_and_reuses_session(
        self, mock_config_get, mock_html_get
    ):
        """Test ABB search initializes and reuses a request session for cookie continuity."""
        mock_config_get.return_value = 0.0
        mock_html_get.side_effect = [
            ("", "https://audiobookbay.lu/"),  # Bootstrap attempt
            (SAMPLE_SEARCH_HTML, "https://audiobookbay.lu/?s=test&cat=undefined%2Cundefined"),
        ]

        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")

        assert len(results) == 2
        assert mock_html_get.call_count == 2
        bootstrap_call = mock_html_get.call_args_list[0]
        search_call = mock_html_get.call_args_list[1]
        assert bootstrap_call.args[0] == "https://audiobookbay.lu/"
        assert search_call.args[0].startswith("https://audiobookbay.lu/?s=test")
        assert bootstrap_call.kwargs["session"] is not None
        assert search_call.kwargs["session"] is bootstrap_call.kwargs["session"]

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_empty(self, mock_config_get, mock_html_get):
        """Test search with no results."""
        mock_config_get.return_value = 1.0
        mock_html_get.return_value = (
            EMPTY_SEARCH_HTML,
            "https://audiobookbay.lu/page/1/?s=test&cat=undefined%2Cundefined",
        )

        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")

        assert len(results) == 0

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_error_non_200(self, mock_config_get, mock_html_get):
        """Test error handling for non-200 status code."""
        mock_config_get.return_value = 1.0
        mock_html_get.return_value = (
            "",
            "https://audiobookbay.lu/page/1/?s=test&cat=undefined%2Cundefined",
        )

        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")

        assert len(results) == 0

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_redirect_to_homepage(self, mock_config_get, mock_html_get):
        """Test handling redirect to homepage (blocked/invalid search)."""
        mock_config_get.return_value = 1.0
        mock_html_get.return_value = (
            EMPTY_SEARCH_HTML,
            "https://audiobookbay.lu",  # Redirected to homepage
        )

        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")

        assert len(results) == 0

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_request_exception(self, mock_config_get, mock_html_get):
        """Test handling request exceptions."""
        mock_config_get.return_value = 1.0
        mock_html_get.return_value = (
            "",
            "https://audiobookbay.lu/page/1/?s=test&cat=undefined%2Cundefined",
        )

        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")

        assert len(results) == 0

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_relative_link(self, mock_config_get, mock_html_get):
        """Test handling relative links in results."""
        mock_config_get.return_value = 1.0

        html_with_relative_link = """
        <div class="post">
            <div class="postTitle"><h2><a href="/abss/relative-link/">Test Book</a></h2></div>
            <div class="postInfo">Language: English</div>
            <div class="postContent">
                <p style="text-align:center;">Posted: 01 Jan 2024<br>Format: M4B<br>File Size: 100 MBs</p>
            </div>
        </div>
        """

        mock_html_get.return_value = (
            html_with_relative_link,
            "https://audiobookbay.lu/page/1/?s=test&cat=undefined%2Cundefined",
        )

        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")

        assert len(results) == 1
        assert results[0]["link"] == "https://audiobookbay.lu/abss/relative-link/"

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_protocol_relative_links(self, mock_config_get, mock_html_get):
        """Test protocol-relative links are normalized without duplicating hostname."""
        mock_config_get.return_value = 0.0

        html_with_protocol_relative_links = """
        <div class="post">
            <div class="postTitle"><h2><a href="//audiobookbay.lu/abss/protocol-relative/">Protocol Relative Book</a></h2></div>
            <div class="postInfo">Language: English</div>
            <div class="postContent">
                <div class="center">
                    <img src="//audiobookbay.lu/wp-content/uploads/cover.jpg" alt="Cover" />
                </div>
                <p style="text-align:center;">Posted: 01 Jan 2024<br>Format: M4B<br>File Size: 100 MBs</p>
            </div>
        </div>
        """

        mock_html_get.return_value = (
            html_with_protocol_relative_links,
            "https://audiobookbay.lu/?s=test",
        )

        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")

        assert len(results) == 1
        assert results[0]["link"] == "https://audiobookbay.lu/abss/protocol-relative/"
        assert results[0]["cover"] == "https://audiobookbay.lu/wp-content/uploads/cover.jpg"

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_exact_phrase_query(self, mock_config_get, mock_html_get):
        """Test exact phrase wrapping and encoding in search URL."""
        mock_config_get.return_value = 0.0
        mock_html_get.return_value = (
            SAMPLE_SEARCH_HTML,
            "https://audiobookbay.lu/page/1/?s=%22test+query%22",
        )

        results = scraper.search_audiobookbay(
            "test query",
            max_pages=1,
            hostname="audiobookbay.lu",
            exact_phrase=True,
        )

        assert len(results) == 2
        requested_url = mock_html_get.call_args.args[0]
        assert "s=%22test+query%22" in requested_url
        assert "cat=undefined%2Cundefined" in requested_url

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_normalizes_curly_apostrophe(self, mock_config_get, mock_html_get):
        """Test curly apostrophes are searched as the ASCII form ABB stores."""
        mock_config_get.return_value = 0.0
        mock_html_get.return_value = (SAMPLE_SEARCH_HTML, "https://audiobookbay.lu/?s=x")

        scraper.search_audiobookbay(
            "the stranger’s wife",
            max_pages=1,
            hostname="audiobookbay.lu",
        )

        requested_url = mock_html_get.call_args.args[0]
        assert "s=the+stranger%27s+wife" in requested_url
        assert "%E2%80%99" not in requested_url

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_percent_encodes_reserved_characters(
        self, mock_config_get, mock_html_get
    ):
        """Test reserved characters cannot break out of the search parameter."""
        mock_config_get.return_value = 0.0
        mock_html_get.return_value = (SAMPLE_SEARCH_HTML, "https://audiobookbay.lu/?s=x")

        scraper.search_audiobookbay(
            "sense & sensibility 100% c++",
            max_pages=1,
            hostname="audiobookbay.lu",
        )

        requested_url = mock_html_get.call_args.args[0]
        assert "s=sense+%26+sensibility+100%25+c%2B%2B" in requested_url
        # The only surviving '&' introduces the legacy category parameter.
        assert requested_url.count("&") == 1
        assert requested_url.endswith("&cat=undefined%2Cundefined")

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    @patch("shelfmark.release_sources.audiobookbay.scraper.config.get")
    def test_search_audiobookbay_always_uses_legacy_category_query(
        self, mock_config_get, mock_html_get
    ):
        """Test ABB search always includes legacy category query and does not fallback."""
        mock_config_get.return_value = 0.0
        mock_html_get.return_value = (
            "",
            "https://audiobookbay.lu/?s=test&cat=undefined%2Cundefined",
        )

        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")

        assert len(results) == 0
        assert mock_html_get.call_count >= 2
        search_urls = [
            call.args[0] for call in mock_html_get.call_args_list if "?s=test" in call.args[0]
        ]
        assert search_urls
        assert all(
            url == "https://audiobookbay.lu/?s=test&cat=undefined%2Cundefined"
            for url in search_urls
        )


class TestExtractMagnetLink:
    """Tests for the extract_magnet_link function."""

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    def test_extract_magnet_link_success(self, mock_html_get):
        """Test successful magnet link extraction."""
        mock_html_get.side_effect = [
            ("", "https://audiobookbay.lu/"),  # Bootstrap attempt
            SAMPLE_DETAIL_HTML,
        ]

        magnet_link = scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/", hostname="audiobookbay.lu"
        )

        assert magnet_link is not None
        assert magnet_link.startswith("magnet:?xt=urn:btih:")
        assert "ABC123DEF456789012345678901234567890ABCD" in magnet_link
        assert "udp%3A//tracker.openbittorrent.com%3A80" in magnet_link
        assert "http%3A//tracker.example.com%3A8080" in magnet_link
        assert mock_html_get.call_count == 2

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    def test_extract_magnet_link_reuses_bootstrap_session(self, mock_html_get):
        """Test detail page fetch reuses the bootstrap session for ABB cookies."""
        mock_html_get.side_effect = [
            ("", "https://audiobookbay.lu/"),
            SAMPLE_DETAIL_HTML,
        ]

        scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/", hostname="audiobookbay.lu"
        )

        assert mock_html_get.call_count == 2
        bootstrap_call = mock_html_get.call_args_list[0]
        detail_call = mock_html_get.call_args_list[1]
        assert bootstrap_call.args[0] == "https://audiobookbay.lu/"
        assert detail_call.args[0] == "https://audiobookbay.lu/abss/test-book/"
        assert detail_call.kwargs["session"] is bootstrap_call.kwargs["session"]

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    def test_extract_magnet_link_fallback(self, mock_html_get):
        """Test fallback to default trackers when none found."""
        mock_html_get.return_value = DETAIL_HTML_NO_TRACKERS

        magnet_link = scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/", hostname="audiobookbay.lu"
        )

        assert magnet_link is not None
        assert magnet_link.startswith("magnet:?xt=urn:btih:")
        assert "ABC123DEF456789012345678901234567890ABCD" in magnet_link
        # Should contain default trackers
        assert "udp%3A//tracker.openbittorrent.com%3A80" in magnet_link

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    def test_extract_magnet_link_no_info_hash(self, mock_html_get):
        """Test handling missing info hash."""
        mock_html_get.return_value = "<html><body></body></html>"

        magnet_link = scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/", hostname="audiobookbay.lu"
        )

        assert magnet_link is None

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    def test_extract_magnet_link_non_200(self, mock_html_get):
        """Test handling non-200 status code."""
        mock_html_get.return_value = ""

        magnet_link = scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/", hostname="audiobookbay.lu"
        )

        assert magnet_link is None

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    def test_extract_magnet_link_request_exception(self, mock_html_get):
        """Test handling request exceptions."""
        mock_html_get.return_value = ""

        magnet_link = scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/", hostname="audiobookbay.lu"
        )

        assert magnet_link is None

    @patch("shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page")
    def test_extract_magnet_link_cleans_info_hash(self, mock_html_get):
        """Test that info hash whitespace is cleaned."""
        html_with_whitespace = """
        <html>
        <body>
        <table>
            <tr>
                <td>Info Hash</td>
                <td>ABC 123 DEF 456 789 012 345 678 901 234 567 890 ABC D</td>
            </tr>
        </table>
        </body>
        </html>
        """

        mock_html_get.return_value = html_with_whitespace

        magnet_link = scraper.extract_magnet_link(
            "https://audiobookbay.lu/abss/test-book/", hostname="audiobookbay.lu"
        )

        assert magnet_link is not None
        # Info hash should be cleaned (no spaces, uppercase)
        assert "ABC123DEF456" in magnet_link


def _post_with_comments(*comments: str) -> str:
    bodies = "".join(
        f'<li itemprop="review"><div itemprop="reviewBody"><p>{text}</p></div></li>'
        for text in comments
    )
    return f"<html><body><p>Seed it please, this page text is not a comment</p><ul>{bodies}</ul></body></html>"


def test_seed_complaints_counts_the_comments_that_say_nobody_is_seeding():
    html = _post_with_comments(
        "Please seed and thank you",
        "I think you have a problem, as it has 0 seeders. Can somebody please seed or reup?",
        "No seeds :(",
        "Great narrator, thanks for the upload",
    )

    assert scraper.extract_seed_complaints(html) == 3


def test_seed_complaints_ignores_page_text_outside_the_comments():
    assert scraper.extract_seed_complaints(_post_with_comments("Loved it")) == 0


def test_seed_complaints_reads_dead_and_unseeded():
    html = _post_with_comments("link is dead", "all of these are unseeded", "worked for me")

    assert scraper.extract_seed_complaints(html) == 2
