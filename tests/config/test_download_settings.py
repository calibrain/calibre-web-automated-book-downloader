from unittest.mock import patch


def _base_email_mode_values() -> dict[str, object]:
    return {
        "BOOKS_OUTPUT_MODE": "email",
        "EMAIL_SMTP_HOST": "smtp.example.com",
        "EMAIL_FROM": "Shelfmark <mail@example.com>",
    }


def test_on_save_downloads_allows_empty_default_email_recipient(monkeypatch):
    from shelfmark.config.settings import _on_save_downloads

    monkeypatch.setattr("shelfmark.config.settings.load_config_file", lambda _tab: {})

    values = {
        **_base_email_mode_values(),
        "EMAIL_RECIPIENT": "",
    }

    result = _on_save_downloads(values)

    assert result["error"] is False
    assert result["values"]["EMAIL_RECIPIENT"] == ""


def test_on_save_downloads_validates_default_email_recipient_format(monkeypatch):
    from shelfmark.config.settings import _on_save_downloads

    monkeypatch.setattr("shelfmark.config.settings.load_config_file", lambda _tab: {})

    values = {
        **_base_email_mode_values(),
        "EMAIL_RECIPIENT": "Reader <reader@example.com>",
    }

    result = _on_save_downloads(values)

    assert result["error"] is True
    assert "valid plain email address" in result["message"]


def test_download_settings_email_recipient_field_uses_default_label():
    from shelfmark.config.settings import download_settings

    fields = download_settings()
    email_field = next(
        field for field in fields if getattr(field, "key", None) == "EMAIL_RECIPIENT"
    )

    assert email_field.label == "Default Email Recipient"
    assert "Optional fallback" in email_field.description


def test_download_settings_booklore_destination_field_defaults_to_library():
    from shelfmark.config.settings import download_settings

    fields = download_settings()
    destination_field = next(
        field for field in fields if getattr(field, "key", None) == "BOOKLORE_DESTINATION"
    )

    assert destination_field.default == "library"
    option_values = {option["value"] for option in destination_field.options}
    assert option_values == {"library", "bookdrop"}


def test_download_settings_audiobook_grouping_is_opt_in():
    from shelfmark.config.settings import download_settings

    fields = download_settings()
    organization_field = next(
        field for field in fields if getattr(field, "key", None) == "FILE_ORGANIZATION_AUDIOBOOK"
    )
    rename_template = next(
        field
        for field in fields
        if getattr(field, "key", None) == "template_audiobook_rename_editor"
    )

    assert organization_field.default == "rename"
    assert [option["value"] for option in organization_field.options] == [
        "none",
        "rename",
        "organize",
        "rename_and_group",
    ]
    assert rename_template.show_when == {
        "field": "FILE_ORGANIZATION_AUDIOBOOK",
        "value": ["rename", "rename_and_group"],
    }


def test_download_settings_grimmory_copy_is_exposed_in_ui_metadata():
    from shelfmark.config.settings import download_settings

    fields = download_settings()

    output_mode_field = next(
        field for field in fields if getattr(field, "key", None) == "BOOKS_OUTPUT_MODE"
    )
    grimmory_option = next(
        option for option in output_mode_field.options if option["value"] == "booklore"
    )
    heading_field = next(
        field for field in fields if getattr(field, "key", None) == "booklore_heading"
    )
    url_field = next(field for field in fields if getattr(field, "key", None) == "BOOKLORE_HOST")

    assert grimmory_option["label"] == "Grimmory (API)"
    assert grimmory_option["description"] == "Upload files directly to Grimmory"
    assert heading_field.title == "Grimmory"
    assert "(Formerly Booklore)" in heading_field.description
    assert url_field.label == "Grimmory URL"


def test_download_settings_booklore_library_and_path_depend_on_library_destination():
    from shelfmark.config.settings import download_settings

    fields = download_settings()
    library_field = next(
        field for field in fields if getattr(field, "key", None) == "BOOKLORE_LIBRARY_ID"
    )
    path_field = next(
        field for field in fields if getattr(field, "key", None) == "BOOKLORE_PATH_ID"
    )

    assert library_field.show_when == [
        {"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
        {"field": "BOOKLORE_DESTINATION", "value": "library"},
    ]
    assert path_field.show_when == [
        {"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
        {"field": "BOOKLORE_DESTINATION", "value": "library"},
    ]


def test_download_settings_destination_test_buttons_exist():
    from shelfmark.config.settings import download_settings

    fields = download_settings()
    books_button = next(
        field for field in fields if getattr(field, "key", None) == "test_destination"
    )
    audiobook_button = next(
        field for field in fields if getattr(field, "key", None) == "test_destination_audiobook"
    )

    assert books_button.label == "Test Destination"
    assert books_button.style == "primary"
    assert books_button.show_when == {"field": "BOOKS_OUTPUT_MODE", "value": "folder"}
    assert audiobook_button.label == "Test Destination"
    assert audiobook_button.style == "primary"
    assert audiobook_button.universal_only is True


def test_download_settings_naming_templates_use_wrapped_custom_component():
    from shelfmark.config.settings import download_settings

    fields = download_settings()
    fields_by_key = {getattr(field, "key", None): field for field in fields}

    expected = {
        "template_rename_editor": "TEMPLATE_RENAME",
        "template_organize_editor": "TEMPLATE_ORGANIZE",
        "template_audiobook_rename_editor": "TEMPLATE_AUDIOBOOK_RENAME",
        "template_audiobook_organize_editor": "TEMPLATE_AUDIOBOOK_ORGANIZE",
    }

    for editor_key, value_key in expected.items():
        editor = fields_by_key[editor_key]

        assert editor.get_field_type() == "CustomComponentField"
        assert editor.component == "naming_template"
        assert editor.wrap_in_field_wrapper is True
        assert editor.get_bind_keys() == [value_key]
        assert [field.key for field in editor.value_fields] == [value_key]
        assert editor.label == editor.value_fields[0].label

    for value_key in expected.values():
        assert value_key not in fields_by_key


def test_download_settings_naming_template_value_fields_are_registered():
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core import settings_registry

    field_map = settings_registry.get_settings_field_map(tab_name="downloads")

    for value_key in (
        "TEMPLATE_RENAME",
        "TEMPLATE_ORGANIZE",
        "TEMPLATE_AUDIOBOOK_RENAME",
        "TEMPLATE_AUDIOBOOK_ORGANIZE",
    ):
        assert value_key in field_map


def test_download_settings_naming_template_serialization_keeps_value_fields_hidden():
    from shelfmark.config.settings import download_settings
    from shelfmark.core import settings_registry
    from shelfmark.core.settings_registry import SettingsTab

    tab = SettingsTab(name="downloads", display_name="Downloads", fields=download_settings())
    serialized_tab = settings_registry.serialize_tab(tab)
    serialized_fields = {field["key"]: field for field in serialized_tab["fields"]}

    editor = serialized_fields["template_organize_editor"]
    bound_fields = editor.get("boundFields", [])

    assert editor["component"] == "naming_template"
    assert editor["wrapInFieldWrapper"] is True
    assert editor["bindKeys"] == ["TEMPLATE_ORGANIZE"]
    assert [field["key"] for field in bound_fields] == ["TEMPLATE_ORGANIZE"]
    assert bound_fields[0]["hiddenInUi"] is True
    assert bound_fields[0]["placeholder"] == "{Author}/{Series/}{Title} ({Year})"


def test_test_books_destination_uses_current_values(tmp_path):
    from shelfmark.config.download_settings_handlers import check_books_destination

    destination = tmp_path / "books"

    result = check_books_destination({"DESTINATION": str(destination)})

    assert result["success"] is True
    assert result["message"] == f"Books destination is writable: {destination}"
    assert destination.exists()


def test_test_audiobook_destination_falls_back_to_books_destination(tmp_path):
    from shelfmark.config.download_settings_handlers import check_audiobook_destination

    destination = tmp_path / "books"

    result = check_audiobook_destination(
        {
            "DESTINATION": str(destination),
            "DESTINATION_AUDIOBOOK": "",
        }
    )

    assert result["success"] is True
    assert result["message"] == (
        f"Audiobook destination is writable: {destination} (using the Books destination)"
    )


def test_test_books_destination_uses_base_path_for_user_placeholder(tmp_path):
    from shelfmark.config.download_settings_handlers import check_books_destination

    destination = tmp_path / "books"

    result = check_books_destination({"DESTINATION": f"{destination}/{{User}}"})

    assert result["success"] is True
    assert result["message"] == (
        f"Books destination is writable: {destination} "
        f"(tested base path {destination} from configured template {destination}/{{User}})"
    )
    assert not (destination / "{User}").exists()


def test_test_books_destination_uses_base_path_for_lowercase_user_placeholder(tmp_path):
    from shelfmark.config.download_settings_handlers import check_books_destination

    destination = tmp_path / "books"

    result = check_books_destination({"DESTINATION": f"{destination}/{{user}}"})

    assert result["success"] is True
    assert result["message"] == (
        f"Books destination is writable: {destination} "
        f"(tested base path {destination} from configured template {destination}/{{user}})"
    )
    assert not (destination / "{user}").exists()


def test_test_books_destination_rejects_relative_user_placeholder_path():
    from shelfmark.config.download_settings_handlers import check_books_destination

    result = check_books_destination({"DESTINATION": "{User}/books"})

    assert result["success"] is False
    assert result["message"] == "Destination must be absolute: {User}/books"


def test_test_books_destination_requires_value():
    from shelfmark.config.download_settings_handlers import check_books_destination

    result = check_books_destination({"DESTINATION": ""})

    assert result["success"] is False
    assert result["message"] == "Books destination is required"


def test_test_books_destination_uses_persisted_value_when_current_values_missing(
    monkeypatch, tmp_path
):
    from shelfmark.config.download_settings_handlers import check_books_destination
    from shelfmark.core.config import config

    destination = tmp_path / "persisted-books"

    def _fake_get(key: str, default=None):
        if key == "DESTINATION":
            return str(destination)
        return default

    monkeypatch.setattr(config, "get", _fake_get)

    result = check_books_destination()

    assert result["success"] is True
    assert result["message"] == f"Books destination is writable: {destination}"


def test_test_audiobook_destination_preserves_books_fallback_suffix_on_failure(tmp_path):
    from shelfmark.config.download_settings_handlers import check_audiobook_destination

    destination = tmp_path / "books"

    def _fake_validate_destination(path, status_callback):
        status_callback("error", f"Destination not writable: {path}")
        return False

    with patch(
        "shelfmark.download.postprocess.destination.validate_destination",
        side_effect=_fake_validate_destination,
    ):
        result = check_audiobook_destination(
            {
                "DESTINATION": str(destination),
                "DESTINATION_AUDIOBOOK": "",
            }
        )

    assert result["success"] is False
    assert result["message"] == (
        f"Destination not writable: {destination} (using the Books destination)"
    )


def test_execute_action_passes_unsaved_values_to_destination_test(tmp_path):
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core.settings_registry import execute_action

    destination = tmp_path / "action-books"
    captured: dict[str, object] = {}

    def _fake_validate_destination(path, status_callback):
        captured["path"] = path
        return True

    with patch(
        "shelfmark.download.postprocess.destination.validate_destination",
        side_effect=_fake_validate_destination,
    ):
        result = execute_action(
            "downloads",
            "test_destination",
            {"DESTINATION": str(destination)},
        )

    assert result["success"] is True
    assert captured["path"] == destination


def test_search_mode_defaults_to_universal_and_direct_mentions_configuration():
    from shelfmark.config.settings import search_mode_settings

    fields = search_mode_settings()
    search_mode_field = next(
        field for field in fields if getattr(field, "key", None) == "SEARCH_MODE"
    )
    direct_option = next(
        option for option in search_mode_field.options if option["value"] == "direct"
    )

    assert search_mode_field.default == "universal"
    assert "mirror URLs" in direct_option["description"]


def test_download_source_settings_include_direct_download_toggle():
    from shelfmark.config.settings import download_source_settings

    fields = download_source_settings()
    toggle_field = next(
        field for field in fields if getattr(field, "key", None) == "DIRECT_DOWNLOAD_ENABLED"
    )

    assert toggle_field.default is False
    assert "Add your own mirror URLs" in toggle_field.description


def test_download_source_settings_include_distant_path_language_toggle():
    from shelfmark.config.settings import download_source_settings

    fields = download_source_settings()
    toggle_field = next(
        field
        for field in fields
        if getattr(field, "key", None) == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH"
    )

    assert toggle_field.default is False
    assert "distant path" in toggle_field.description.lower()


def test_fast_source_options_lock_entries_without_mirror_or_donator_requirements(monkeypatch):
    from shelfmark.config.settings import _get_fast_source_options

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        values = {
            "AA_DONATOR_KEY": "",
        }
        return values.get(key, default)

    monkeypatch.setattr("shelfmark.core.config.config.get", _fake_get)
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: False)
    monkeypatch.setattr("shelfmark.core.mirrors.has_libgen_mirror_configuration", lambda: True)

    options = {option["id"]: option for option in _get_fast_source_options()}

    assert options["aa-fast"]["isLocked"] is True
    assert (
        options["aa-fast"]["disabledReason"] == "Add at least one Anna's Archive mirror in Mirrors"
    )
    assert options["libgen"]["isLocked"] is False
    assert options["libgen"]["disabledReason"] is None


def test_slow_source_options_lock_entries_until_mirror_dependencies_exist(monkeypatch):
    from shelfmark.config.settings import _get_slow_source_options

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        values = {
            "USE_CF_BYPASS": True,
        }
        return values.get(key, default)

    monkeypatch.setattr("shelfmark.core.config.config.get", _fake_get)
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: True)
    monkeypatch.setattr("shelfmark.core.mirrors.has_welib_mirror_configuration", lambda: False)
    monkeypatch.setattr("shelfmark.core.mirrors.has_zlib_mirror_configuration", lambda: False)
    monkeypatch.setattr("shelfmark.core.mirrors.has_oceanofpdf_mirror_configuration", lambda: False)

    options = {option["id"]: option for option in _get_slow_source_options()}

    assert options["aa-slow-nowait"]["isLocked"] is False
    assert options["aa-slow-wait"]["isLocked"] is False
    assert options["welib"]["isLocked"] is True
    assert options["welib"]["disabledReason"] == "Add at least one Welib mirror in Mirrors"
    assert options["zlib"]["isLocked"] is True
    assert options["zlib"]["disabledReason"] == "Add at least one Z-Library mirror in Mirrors"
    assert options["oceanofpdf"]["isLocked"] is True
    assert options["oceanofpdf"]["disabledReason"] == (
        "Add at least one OceanofPDF mirror in Mirrors"
    )


def test_oceanofpdf_slow_source_only_requires_its_mirror(monkeypatch):
    from shelfmark.config.settings import _get_slow_source_defaults, _get_slow_source_options

    monkeypatch.setattr(
        "shelfmark.core.config.config.get",
        lambda key, default=None, user_id=None: False if key == "USE_CF_BYPASS" else default,
    )
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: True)
    monkeypatch.setattr("shelfmark.core.mirrors.has_welib_mirror_configuration", lambda: True)
    monkeypatch.setattr("shelfmark.core.mirrors.has_zlib_mirror_configuration", lambda: True)
    monkeypatch.setattr("shelfmark.core.mirrors.has_oceanofpdf_mirror_configuration", lambda: True)

    options = {option["id"]: option for option in _get_slow_source_options()}
    defaults = {item["id"]: item["enabled"] for item in _get_slow_source_defaults()}

    assert options["oceanofpdf"]["isLocked"] is False
    assert options["oceanofpdf"]["disabledReason"] is None
    assert defaults["oceanofpdf"] is True
