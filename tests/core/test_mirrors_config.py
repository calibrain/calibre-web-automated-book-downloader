from shelfmark.core import mirrors


class _DummyConfig:
    def __init__(self, values: dict):
        self._values = values

    def get(self, key: str, default=None):
        return self._values.get(key, default)


def test_get_aa_mirrors_prefers_full_configured_list(monkeypatch):
    dummy = _DummyConfig(
        {
            "AA_MIRROR_URLS": ["annas-archive.gl/", "https://annas-archive.li"],
            "AA_ADDITIONAL_URLS": "https://should-not-be-appended.example",
        }
    )
    monkeypatch.setattr(mirrors, "_get_config", lambda: dummy)

    assert mirrors.get_aa_mirrors() == [
        "https://annas-archive.gl",
        "https://annas-archive.li",
    ]


def test_get_aa_mirrors_uses_legacy_additional_when_no_explicit_list(monkeypatch):
    dummy = _DummyConfig(
        {
            "AA_MIRROR_URLS": [],
            "AA_ADDITIONAL_URLS": "extra.example, https://extra2.example/",
        }
    )
    monkeypatch.setattr(mirrors, "_get_config", lambda: dummy)

    assert mirrors.get_aa_mirrors() == [
        "https://extra.example",
        "https://extra2.example",
    ]


def test_get_aa_mirrors_returns_empty_when_unconfigured(monkeypatch):
    monkeypatch.setattr(mirrors, "_get_config", lambda: _DummyConfig({}))

    assert mirrors.get_aa_mirrors() == []


def test_has_aa_mirror_configuration_accepts_custom_base_url_without_list(monkeypatch):
    dummy = _DummyConfig(
        {
            "AA_BASE_URL": "https://custom-aa.example",
            "AA_MIRROR_URLS": [],
        }
    )
    monkeypatch.setattr(mirrors, "_get_config", lambda: dummy)

    assert mirrors.has_aa_mirror_configuration() is True


def test_get_libgen_mirrors_returns_user_supplied_urls_only(monkeypatch):
    dummy = _DummyConfig({"LIBGEN_MIRROR_URLS": ["libgen.one", "https://libgen.two/"]})
    monkeypatch.setattr(mirrors, "_get_config", lambda: dummy)

    assert mirrors.get_libgen_mirrors() == [
        "https://libgen.one",
        "https://libgen.two",
    ]


def test_get_libgen_mirrors_falls_back_to_legacy_config(monkeypatch):
    dummy = _DummyConfig({"LIBGEN_ADDITIONAL_URLS": "libgen.legacy, https://libgen.backup/"})
    monkeypatch.setattr(mirrors, "_get_config", lambda: dummy)

    assert mirrors.get_libgen_mirrors() == [
        "https://libgen.legacy",
        "https://libgen.backup",
    ]


def test_get_zlib_mirrors_prefers_canonical_list(monkeypatch):
    dummy = _DummyConfig({"ZLIB_MIRROR_URLS": ["zlib.primary", "https://zlib.backup"]})
    monkeypatch.setattr(mirrors, "_get_config", lambda: dummy)

    assert mirrors.get_zlib_mirrors() == [
        "https://zlib.primary",
        "https://zlib.backup",
    ]
    assert mirrors.get_zlib_url_template() == "https://zlib.primary/md5/{md5}"


def test_get_zlib_mirrors_falls_back_to_primary_then_additional(monkeypatch):
    dummy = _DummyConfig(
        {
            "ZLIB_PRIMARY_URL": "zlib.primary",
            "ZLIB_ADDITIONAL_URLS": "https://zlib.primary, zlib.backup",
        }
    )
    monkeypatch.setattr(mirrors, "_get_config", lambda: dummy)

    assert mirrors.get_zlib_mirrors() == [
        "https://zlib.primary",
        "https://zlib.backup",
    ]
    assert mirrors.get_zlib_url_template() == "https://zlib.primary/md5/{md5}"


def test_get_zlib_url_template_returns_none_without_config(monkeypatch):
    monkeypatch.setattr(mirrors, "_get_config", lambda: _DummyConfig({}))

    assert mirrors.get_zlib_url_template() is None


def test_get_welib_mirrors_prefers_primary_then_additional(monkeypatch):
    dummy = _DummyConfig({"WELIB_MIRROR_URLS": ["welib.primary", "https://welib.backup"]})
    monkeypatch.setattr(mirrors, "_get_config", lambda: dummy)

    assert mirrors.get_welib_mirrors() == [
        "https://welib.primary",
        "https://welib.backup",
    ]
    assert mirrors.get_welib_url_template() == "https://welib.primary/md5/{md5}"


def test_get_welib_mirrors_falls_back_to_primary_then_additional(monkeypatch):
    dummy = _DummyConfig(
        {
            "WELIB_PRIMARY_URL": "welib.primary",
            "WELIB_ADDITIONAL_URLS": "https://welib.backup",
        }
    )
    monkeypatch.setattr(mirrors, "_get_config", lambda: dummy)

    assert mirrors.get_welib_mirrors() == [
        "https://welib.primary",
        "https://welib.backup",
    ]
    assert mirrors.get_welib_url_template() == "https://welib.primary/md5/{md5}"


def test_get_welib_url_template_returns_none_without_config(monkeypatch):
    monkeypatch.setattr(mirrors, "_get_config", lambda: _DummyConfig({}))

    assert mirrors.get_welib_url_template() is None


def test_oceanofpdf_mirrors_normalize_and_use_first_url(monkeypatch):
    dummy = _DummyConfig(
        {"OCEANOFPDF_MIRROR_URLS": "books.example/, https://books.example, https://backup.example/"}
    )
    monkeypatch.setattr(mirrors, "_get_config", lambda: dummy)

    assert mirrors.get_oceanofpdf_mirrors() == ["https://books.example", "https://backup.example"]
    assert mirrors.get_oceanofpdf_primary_url() == "https://books.example"
    assert mirrors.has_download_source_mirror_configuration("oceanofpdf") is True
    assert mirrors.get_download_source_missing_mirror_reason("oceanofpdf") is None


def test_oceanofpdf_has_no_default_mirrors(monkeypatch):
    monkeypatch.setattr(mirrors, "_get_config", lambda: _DummyConfig({}))

    assert mirrors.get_oceanofpdf_mirrors() == []
    assert mirrors.get_oceanofpdf_primary_url() is None
    assert mirrors.has_download_source_mirror_configuration("oceanofpdf") is False
    assert mirrors.get_download_source_missing_mirror_reason("oceanofpdf") == (
        "Add at least one OceanofPDF mirror in Mirrors"
    )
