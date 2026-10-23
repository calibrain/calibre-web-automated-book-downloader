"""One default for the Supported Book Formats setting, read the same way everywhere."""

import pytest

from shelfmark.config import settings as app_settings
from shelfmark.core.utils import DEFAULT_EBOOK_FORMATS, normalize_format_list
from shelfmark.download.postprocess import policy

DEFAULT = ["epub", "mobi"]


class TestNormalizeFormatList:
    def test_a_string_splits_on_commas_and_spaces_and_drops_leading_dots(self):
        assert normalize_format_list("epub, .MOBI azw3", DEFAULT) == ["epub", "mobi", "azw3"]

    def test_a_list_is_lowercased_and_stripped(self):
        assert normalize_format_list([" EPUB ", ".mobi", "", "  "], DEFAULT) == ["epub", "mobi"]

    @pytest.mark.parametrize("value", ["", " , ", [], [""], (), set()])
    def test_nothing_left_falls_back_to_the_default(self, value):
        assert normalize_format_list(value, DEFAULT) == DEFAULT

    @pytest.mark.parametrize("value", [None, 3, 1.5, {"epub": 1}, object()])
    def test_a_value_of_the_wrong_kind_falls_back_to_the_default(self, value):
        assert normalize_format_list(value, DEFAULT) == DEFAULT

    def test_the_default_is_returned_as_a_new_list(self):
        assert normalize_format_list(None, DEFAULT_EBOOK_FORMATS) == list(DEFAULT_EBOOK_FORMATS)


def test_the_registry_default_is_the_shared_default():
    field = next(
        f for f in app_settings.general_settings() if getattr(f, "key", "") == "SUPPORTED_FORMATS"
    )

    assert field.default == list(DEFAULT_EBOOK_FORMATS)


def test_post_processing_falls_back_to_the_shared_default(monkeypatch):
    class _Config:
        def get(self, key, default=None, user_id=None):
            return default

    monkeypatch.setattr("shelfmark.core.config.config", _Config())

    assert policy.get_supported_formats() == list(DEFAULT_EBOOK_FORMATS)
