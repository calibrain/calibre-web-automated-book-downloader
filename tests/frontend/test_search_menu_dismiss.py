"""
Tests for frontend search menu behavior.

These tests verify that the search menu dismissal logic works correctly,
particularly that clicking on the AdvancedFilters panel does not close
the main search menu (issue #1301).
"""

from pathlib import Path

# Path to the frontend source code
FRONTEND_SRC_DIR = Path(__file__).parent.parent.parent / "src" / "frontend" / "src"


class TestSearchMenuDismiss:
    """Test that the search menu dismissal behavior is correct."""

    def test_advanced_filters_prevents_mousedown_bubbling(self):
        """
        Verify that AdvancedFilters prevents mousedown events from bubbling
        to the document level, which would otherwise close the main search menu.

        This is the fix for issue #1301: when clicking on dropdown options
        inside AdvancedFilters, the main search menu was collapsing because
        the mousedown event bubbled up to the document and triggered the
        useDismiss hook.

        The fix adds onMouseDown={(e) => e.stopPropagation()} to the
        AdvancedFilters container div.
        """
        advanced_filters_path = FRONTEND_SRC_DIR / "components" / "AdvancedFilters.tsx"

        # Read the component source
        source_code = advanced_filters_path.read_text()

        # Verify that the onMouseDown stopPropagation handler is present
        # This prevents the mousedown event from bubbling up to the document
        # and triggering the useDismiss hook in SearchBar
        assert "onMouseDown={(e) => e.stopPropagation()}" in source_code, (
            "AdvancedFilters must prevent mousedown events from bubbling up "
            "to the document. Without this, clicking on dropdown options inside "
            "AdvancedFilters will close the main search menu (issue #1301)."
        )

    def test_advanced_filters_container_has_onmousedown(self):
        """
        Verify that the settingsForm div in AdvancedFilters has the
        onMouseDown handler to prevent event bubbling.
        """
        advanced_filters_path = FRONTEND_SRC_DIR / "components" / "AdvancedFilters.tsx"
        source_code = advanced_filters_path.read_text()

        # The settingsForm div should have onMouseDown handler
        # Check that the div has both className and onMouseDown
        assert "const settingsForm = (" in source_code, (
            "AdvancedFilters should define settingsForm as a JSX element"
        )

        # Verify the onMouseDown is on the settingsForm div (not nested inside another element)
        # by checking that it appears after the settingsForm declaration
        settings_form_start = source_code.find("const settingsForm = (")
        onmousedown_pos = source_code.find("onMouseDown={(e) => e.stopPropagation()}")

        assert settings_form_start != -1, "settingsForm declaration not found"
        assert onmousedown_pos != -1, "onMouseDown handler not found"
        assert onmousedown_pos > settings_form_start, (
            "onMouseDown handler should be inside the settingsForm div"
        )

    def test_searchbar_uses_usedismiss_for_menu_closing(self):
        """
        Verify that SearchBar uses useDismiss to close the main menu
        when clicking outside the controlled refs.
        """
        search_bar_path = FRONTEND_SRC_DIR / "components" / "SearchBar.tsx"
        source_code = search_bar_path.read_text()

        # SearchBar should use useDismiss to handle menu closing
        assert "useDismiss" in source_code, (
            "SearchBar should use useDismiss hook for menu dismissal"
        )

        # The options menu stays open for clicks inside selectorRef or optionsPanelRef.
        # AdvancedFilters renders beside SearchBar, outside both, which is why it stops
        # mousedown from reaching this listener.
        assert "useDismiss(isOptionsOpen, [selectorRef, optionsPanelRef]" in source_code, (
            "SearchBar should dismiss the options menu only outside selectorRef and optionsPanelRef"
        )
