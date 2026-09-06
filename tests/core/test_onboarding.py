from __future__ import annotations

from collections import defaultdict

import shelfmark.config.settings as settings_config
import shelfmark.metadata_providers.googlebooks as googlebooks_provider
import shelfmark.metadata_providers.hardcover as hardcover_provider
import shelfmark.metadata_providers.openlibrary as openlibrary_provider
import shelfmark.release_sources.audiobookbay.settings as audiobookbay_settings
import shelfmark.release_sources.irc.settings as irc_settings
import shelfmark.release_sources.prowlarr.settings as prowlarr_settings

_REGISTERED_SETTINGS_MODULES = (
    settings_config,
    googlebooks_provider,
    hardcover_provider,
    openlibrary_provider,
    audiobookbay_settings,
    irc_settings,
    prowlarr_settings,
)


def _group_save_calls(
    save_calls: list[tuple[str, dict[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for tab_name, payload in save_calls:
        grouped[tab_name].append(payload)
    return grouped


def test_get_onboarding_config_uses_release_source_settings_pages():
    from shelfmark.core.onboarding import ONBOARDING_RELEASE_SOURCES_KEY, get_onboarding_config

    config = get_onboarding_config()
    steps = {step["id"]: step for step in config["steps"]}

    assert "release_sources" in steps
    assert "direct_download_setup" in steps
    assert "direct_download_cloudflare_bypass" in steps
    assert "prowlarr" in steps
    assert "audiobookbay" in steps
    assert "irc" in steps

    release_source_fields = {
        field["key"]: field for field in steps["release_sources"]["fields"] if "key" in field
    }
    direct_download_option = next(
        option
        for option in release_source_fields[ONBOARDING_RELEASE_SOURCES_KEY]["options"]
        if option["value"] == "direct_download"
    )
    assert direct_download_option["description"] == (
        "Configure your own mirror URLs for direct ebook downloads."
    )

    direct_download_field_list = steps["direct_download_setup"]["fields"]
    direct_download_fields = {
        field["key"] for field in direct_download_field_list if "key" in field
    }
    direct_download_bypass_fields = {
        field["key"]
        for field in steps["direct_download_cloudflare_bypass"]["fields"]
        if "key" in field
    }
    prowlarr_fields = {
        field["key"]: field for field in steps["prowlarr"]["fields"] if "key" in field
    }
    audiobookbay_fields = {
        field["key"]: field for field in steps["audiobookbay"]["fields"] if "key" in field
    }

    assert "DIRECT_DOWNLOAD_ENABLED" not in direct_download_fields
    assert "FAST_SOURCES_DISPLAY" not in direct_download_fields
    assert "SOURCE_PRIORITY" not in direct_download_fields
    assert "AA_DONATOR_KEY" in direct_download_fields
    assert "AA_MIRROR_URLS" in direct_download_fields
    assert "OCEANOFPDF_MIRROR_URLS" in direct_download_fields
    direct_download_fields_by_key = {
        field["key"]: field for field in direct_download_field_list if "key" in field
    }
    assert (
        direct_download_fields_by_key["direct_download_setup_onboarding_heading"]["title"]
        == "Choose at least one download source"
    )
    assert (
        direct_download_fields_by_key["AA_DONATOR_KEY"]["label"]
        == "Anna's Archive Donator Key (optional)"
    )
    direct_download_field_keys = [field["key"] for field in direct_download_field_list]
    assert direct_download_field_keys.index("AA_MIRROR_URLS") < direct_download_field_keys.index(
        "AA_DONATOR_KEY"
    )
    assert "LIBGEN_ADDITIONAL_URLS" not in direct_download_fields
    assert "ZLIB_PRIMARY_URL" not in direct_download_fields
    direct_download_groups = {
        group["id"]: group for group in steps["direct_download_setup"]["fieldGroups"]
    }
    assert direct_download_groups["annas_archive"] == {
        "id": "annas_archive",
        "title": "Anna's Archive",
        "description": "Configure mirror URLs and an optional donator API key.",
        "fieldKeys": ["AA_MIRROR_URLS", "AA_DONATOR_KEY"],
        "defaultOpen": True,
    }
    assert direct_download_groups["oceanofpdf"] == {
        "id": "oceanofpdf",
        "title": "OceanOfPDF",
        "description": "Configure the mirror URL used for searches and downloads.",
        "fieldKeys": ["OCEANOFPDF_MIRROR_URLS"],
        "defaultOpen": False,
    }
    assert (
        steps["direct_download_setup_direct_mode"]["fieldGroups"]
        == steps["direct_download_setup"]["fieldGroups"]
    )
    assert "EXT_BYPASSER_TIMEOUT" not in direct_download_bypass_fields
    assert "PROWLARR_ENABLED" not in prowlarr_fields
    assert "PROWLARR_AUTO_EXPAND" not in prowlarr_fields
    assert "ABB_ENABLED" not in audiobookbay_fields
    assert "ABB_PAGE_LIMIT" not in audiobookbay_fields
    assert "showWhen" not in prowlarr_fields["PROWLARR_URL"]
    assert "showWhen" not in audiobookbay_fields["ABB_HOSTNAME"]


def test_save_onboarding_settings_enables_selected_release_sources(monkeypatch):
    import shelfmark.core.config as app_config
    import shelfmark.core.onboarding as onboarding

    save_calls: list[tuple[str, dict[str, object]]] = []

    def _fake_save(tab_name: str, values: dict[str, object]) -> bool:
        save_calls.append((tab_name, dict(values)))
        return True

    monkeypatch.setattr(onboarding, "save_config_file", _fake_save)
    monkeypatch.setattr(onboarding, "mark_onboarding_complete", lambda: True)
    monkeypatch.setattr(app_config.config, "refresh", lambda: None, raising=False)

    result = onboarding.save_onboarding_settings(
        {
            "SEARCH_MODE": "universal",
            "METADATA_PROVIDER": "hardcover",
            "HARDCOVER_API_KEY": "hardcover-api-key",
            onboarding.ONBOARDING_RELEASE_SOURCES_KEY: [
                "direct_download",
                "audiobookbay",
                "prowlarr",
            ],
            "AA_MIRROR_URLS": ["https://annas-archive.example"],
            "PROWLARR_URL": "http://prowlarr:9696",
            "PROWLARR_API_KEY": "secret-key",
            "ABB_HOSTNAME": "audiobookbay.lu",
        }
    )

    grouped_calls = _group_save_calls(save_calls)

    assert result == {"success": True, "message": "Onboarding complete!"}
    assert any(
        payload.get("DIRECT_DOWNLOAD_ENABLED") is True
        for payload in grouped_calls["download_sources"]
    )
    assert any(
        payload.get("AA_MIRROR_URLS") == ["https://annas-archive.example"]
        for payload in grouped_calls["mirrors"]
    )
    assert any(
        payload.get("PROWLARR_ENABLED") is True for payload in grouped_calls["prowlarr_config"]
    )
    assert any(
        payload.get("ABB_ENABLED") is True for payload in grouped_calls["audiobookbay_config"]
    )
    assert any(
        payload.get("PROWLARR_URL") == "http://prowlarr:9696"
        and payload.get("PROWLARR_API_KEY") == "secret-key"
        for payload in grouped_calls["prowlarr_config"]
    )
    assert any(
        payload.get("HARDCOVER_ENABLED") is True
        and payload.get("HARDCOVER_API_KEY") == "hardcover-api-key"
        for payload in grouped_calls["hardcover"]
    )
    assert any(
        payload.get("DEFAULT_RELEASE_SOURCE") == "direct_download"
        and payload.get("DEFAULT_RELEASE_SOURCE_AUDIOBOOK") == "audiobookbay"
        for payload in grouped_calls["search_mode"]
    )
    assert not any(
        onboarding.ONBOARDING_RELEASE_SOURCES_KEY in payload for _, payload in save_calls
    )


def test_save_onboarding_settings_skips_hidden_fields(monkeypatch):
    import shelfmark.core.config as app_config
    import shelfmark.core.onboarding as onboarding

    save_calls: list[tuple[str, dict[str, object]]] = []

    def _fake_save(tab_name: str, values: dict[str, object]) -> bool:
        save_calls.append((tab_name, dict(values)))
        return True

    monkeypatch.setattr(onboarding, "save_config_file", _fake_save)
    monkeypatch.setattr(onboarding, "mark_onboarding_complete", lambda: True)
    monkeypatch.setattr(app_config.config, "refresh", lambda: None, raising=False)

    result = onboarding.save_onboarding_settings(
        {
            "SEARCH_MODE": "direct",
            "USE_CF_BYPASS": True,
            "USING_EXTERNAL_BYPASSER": False,
            "EXT_BYPASSER_URL": "http://should-not-save.example",
            "EXT_BYPASSER_PATH": "/v2",
            "EXT_BYPASSER_TIMEOUT": 120000,
        }
    )

    grouped_calls = _group_save_calls(save_calls)
    bypass_payloads = grouped_calls["cloudflare_bypass"]

    assert result == {"success": True, "message": "Onboarding complete!"}
    assert any(
        payload.get("USE_CF_BYPASS") is True and payload.get("USING_EXTERNAL_BYPASSER") is False
        for payload in bypass_payloads
    )
    assert all("EXT_BYPASSER_URL" not in payload for payload in bypass_payloads)
    assert all("EXT_BYPASSER_PATH" not in payload for payload in bypass_payloads)
    assert all("EXT_BYPASSER_TIMEOUT" not in payload for payload in bypass_payloads)
