from unittest.mock import MagicMock


def test_mirror_settings_use_canonical_tag_lists_for_all_non_base_mirror_sets():
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core.settings_registry import HeadingField, TagListField, get_settings_tab

    tab = get_settings_tab("mirrors")

    assert tab is not None
    fields = {field.key: field for field in tab.fields if hasattr(field, "key")}

    assert isinstance(fields["AA_MIRROR_URLS"], TagListField)
    assert isinstance(fields["LIBGEN_MIRROR_URLS"], TagListField)
    assert isinstance(fields["ZLIB_MIRROR_URLS"], TagListField)
    assert isinstance(fields["WELIB_MIRROR_URLS"], TagListField)
    assert isinstance(fields["OCEANOFPDF_MIRROR_URLS"], TagListField)
    assert isinstance(fields["oceanofpdf_mirrors_heading"], HeadingField)
    assert fields["oceanofpdf_mirrors_heading"].title == "OceanofPDF"
    assert fields["OCEANOFPDF_MIRROR_URLS"].label == "Mirrors"


def test_migrate_mirror_settings_does_not_seed_defaults_on_fresh_install(monkeypatch):
    import shelfmark.core.settings_registry as registry

    saves = MagicMock(return_value=True)

    monkeypatch.setattr(
        registry, "load_config_file", lambda tab_name: {} if tab_name == "mirrors" else {}
    )
    monkeypatch.setattr(registry, "save_config_file", saves)

    registry.migrate_mirror_settings()

    saves.assert_not_called()


def test_migrate_mirror_settings_converts_legacy_additional_urls_to_list(monkeypatch):
    import shelfmark.core.settings_registry as registry

    saves = MagicMock(return_value=True)

    monkeypatch.setattr(
        registry,
        "load_config_file",
        lambda tab_name: (
            {"AA_ADDITIONAL_URLS": "aa.one, https://aa.two/"} if tab_name == "mirrors" else {}
        ),
    )
    monkeypatch.setattr(registry, "save_config_file", saves)

    registry.migrate_mirror_settings()

    saves.assert_called_once_with(
        "mirrors",
        {
            "AA_MIRROR_URLS": ["https://aa.one", "https://aa.two"],
        },
    )


def test_migrate_mirror_settings_preserves_existing_list_without_reseeding(monkeypatch):
    import shelfmark.core.settings_registry as registry

    saves = MagicMock(return_value=True)

    monkeypatch.setattr(
        registry,
        "load_config_file",
        lambda tab_name: (
            {
                "AA_MIRROR_URLS": ["https://annas-archive.existing"],
                "_AA_MIRRORS_DEFAULTS_HASH": "old-hash",
            }
            if tab_name == "mirrors"
            else {}
        ),
    )
    monkeypatch.setattr(registry, "save_config_file", saves)

    registry.migrate_mirror_settings()

    saves.assert_not_called()


def test_migrate_mirror_settings_converts_legacy_split_fields_to_canonical_lists(monkeypatch):
    import shelfmark.core.settings_registry as registry

    saves = MagicMock(return_value=True)

    monkeypatch.setattr(
        registry,
        "load_config_file",
        lambda tab_name: (
            {
                "LIBGEN_ADDITIONAL_URLS": "libgen.one, https://libgen.two/",
                "ZLIB_PRIMARY_URL": "zlib.primary",
                "ZLIB_ADDITIONAL_URLS": "https://zlib.primary, zlib.backup",
                "WELIB_PRIMARY_URL": "welib.primary",
                "WELIB_ADDITIONAL_URLS": "https://welib.backup",
            }
            if tab_name == "mirrors"
            else {}
        ),
    )
    monkeypatch.setattr(registry, "save_config_file", saves)

    registry.migrate_mirror_settings()

    saves.assert_called_once_with(
        "mirrors",
        {
            "LIBGEN_MIRROR_URLS": ["https://libgen.one", "https://libgen.two"],
            "ZLIB_MIRROR_URLS": ["https://zlib.primary", "https://zlib.backup"],
            "WELIB_MIRROR_URLS": ["https://welib.primary", "https://welib.backup"],
        },
    )


def test_canonical_mirror_fields_accept_legacy_env_vars(monkeypatch):
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core.settings_registry import (
        get_setting_value,
        get_settings_tab,
        is_value_from_env,
    )

    monkeypatch.setenv("ZLIB_PRIMARY_URL", "zlib.primary")
    monkeypatch.setenv("ZLIB_ADDITIONAL_URLS", "https://zlib.primary, zlib.backup")

    tab = get_settings_tab("mirrors")

    assert tab is not None
    fields = {field.key: field for field in tab.fields if hasattr(field, "key")}
    zlib_field = fields["ZLIB_MIRROR_URLS"]

    assert is_value_from_env(zlib_field) is True
    assert get_setting_value(zlib_field, "mirrors") == [
        "https://zlib.primary",
        "https://zlib.backup",
    ]


def test_migrate_direct_download_upgrade_enables_direct_download_without_seeding_mirrors(
    monkeypatch,
):
    import shelfmark.core.settings_registry as registry

    saves = MagicMock(return_value=True)

    monkeypatch.setattr(registry, "load_config_file", lambda tab_name: {})
    monkeypatch.setattr(registry, "save_config_file", saves)

    registry.migrate_direct_download_upgrade(existing_install=True)

    saves.assert_called_once_with("download_sources", {"DIRECT_DOWNLOAD_ENABLED": True})


def test_migrate_direct_download_upgrade_skips_fresh_install(monkeypatch):
    import shelfmark.core.settings_registry as registry

    saves = MagicMock(return_value=True)

    monkeypatch.setattr(registry, "save_config_file", saves)

    registry.migrate_direct_download_upgrade(existing_install=False)

    saves.assert_not_called()


def test_migrate_search_page_title_preserves_legacy_title_for_existing_installs(monkeypatch):
    import shelfmark.core.settings_registry as registry

    saves = MagicMock(return_value=True)

    monkeypatch.delenv("SEARCH_PAGE_TITLE", raising=False)
    monkeypatch.setattr(registry, "save_config_file", saves)

    registry.migrate_search_page_title(existing_install=True, had_existing_value=False)

    saves.assert_called_once_with("general", {"SEARCH_PAGE_TITLE": "Book Search & Download"})


def test_migrate_search_page_title_skips_when_value_already_exists(monkeypatch):
    import shelfmark.core.settings_registry as registry

    saves = MagicMock(return_value=True)

    monkeypatch.delenv("SEARCH_PAGE_TITLE", raising=False)
    monkeypatch.setattr(registry, "save_config_file", saves)

    registry.migrate_search_page_title(existing_install=True, had_existing_value=True)

    saves.assert_not_called()


def test_migrate_search_page_title_skips_when_env_var_is_set(monkeypatch):
    import shelfmark.core.settings_registry as registry

    saves = MagicMock(return_value=True)

    monkeypatch.setenv("SEARCH_PAGE_TITLE", "Shelfmark")
    monkeypatch.setattr(registry, "save_config_file", saves)

    registry.migrate_search_page_title(existing_install=True, had_existing_value=False)

    saves.assert_not_called()


def test_save_normalizes_oceanofpdf_mirrors():
    from shelfmark.config.settings import _on_save_mirrors

    result = _on_save_mirrors(
        {"OCEANOFPDF_MIRROR_URLS": ["books.example/", "https://books.example", "auto", ""]}
    )
    assert result["values"]["OCEANOFPDF_MIRROR_URLS"] == ["https://books.example"]
