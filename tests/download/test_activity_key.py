"""activity_key drops the swarm note the torrent handler adds beside a progress message."""

import pytest

from shelfmark.download.activity import activity_key


def _seed_note(seeds, give_up_in):
    # Importing the orchestrator first keeps the handler module out of its import cycle.
    import shelfmark.download.orchestrator  # noqa: F401
    from shelfmark.download.clients.base_handler import ExternalClientHandler

    return ExternalClientHandler._seed_note(seeds, give_up_in)


@pytest.mark.parametrize("seeds", [0, 1, 2, 15, 1200])
@pytest.mark.parametrize("give_up_in", [None, 0.0, 30.0, 61.0, 3599.0])
def test_activity_key_strips_whatever_the_handler_appends(seeds, give_up_in):
    message = f"42%{_seed_note(seeds, give_up_in)}"

    assert activity_key(message) == "42%"


@pytest.mark.parametrize(
    "message",
    ["42%", "Fetching metadata", "Seeding (stalled)", "Paused", "No seeders: 0 found, 1 needed"],
)
def test_activity_key_leaves_other_messages_alone(message):
    assert activity_key(message) == message


def test_activity_key_keeps_none():
    assert activity_key(None) is None
