from scripts.generate_env_docs import generate_env_docs


def test_generated_env_docs_use_canonical_mirror_env_vars() -> None:
    docs = generate_env_docs()

    for canonical_var in (
        "AA_BASE_URL",
        "AA_MIRROR_URLS",
        "LIBGEN_MIRROR_URLS",
        "ZLIB_MIRROR_URLS",
        "WELIB_MIRROR_URLS",
        "OCEANOFPDF_MIRROR_URLS",
    ):
        assert f"`{canonical_var}`" in docs

    for legacy_var in (
        "AA_ADDITIONAL_URLS",
        "LIBGEN_ADDITIONAL_URLS",
        "ZLIB_PRIMARY_URL",
        "ZLIB_ADDITIONAL_URLS",
        "WELIB_PRIMARY_URL",
        "WELIB_ADDITIONAL_URLS",
    ):
        assert f"`{legacy_var}`" not in docs

    assert "https://annas-archive.gl" not in docs


def test_generated_env_docs_describe_mirror_lists_as_comma_separated_strings() -> None:
    docs = generate_env_docs()

    assert (
        "| `AA_MIRROR_URLS` | List the Anna's Archive mirror URLs you want Shelfmark to use. "
        "Type a URL and press Enter to add it. Order matters when Auto is selected. | "
        "string (comma-separated) | _empty list_ |"
    ) in docs
    assert (
        "| `LIBGEN_MIRROR_URLS` | Mirrors are tried in the order you add them until one works. | "
        "string (comma-separated) | _empty list_ |"
    ) in docs


def test_generated_env_docs_include_custom_component_value_fields() -> None:
    docs = generate_env_docs()

    for env_var in (
        "TEMPLATE_RENAME",
        "TEMPLATE_ORGANIZE",
        "TEMPLATE_AUDIOBOOK_RENAME",
        "TEMPLATE_AUDIOBOOK_ORGANIZE",
    ):
        assert f"`{env_var}`" in docs

    assert (
        "| `TEMPLATE_AUDIOBOOK_ORGANIZE` | Use / to create folders. Variables: "
        "{Author}, {Title}, {Year}, {Language}, {User}, {OriginalName} "
        "(source filename without extension), {Series}, {SeriesPosition}, {Subtitle}, "
        "{PrimaryTitle}, {PartNumber}. Use arbitrary prefix/suffix:"
    ) in docs
