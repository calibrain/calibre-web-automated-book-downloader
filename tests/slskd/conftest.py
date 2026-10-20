"""
slskd test configuration.

Stubs out optional heavy dependencies (flask_socketio) that are not available
in lightweight dev/CI environments. These are only needed by the IRC source,
which is unrelated to the slskd plugin under test.
"""

import sys
import types


def _stub_module(name: str) -> None:
    """Insert a minimal stub module into sys.modules if not already present."""
    if name not in sys.modules:
        stub = types.ModuleType(name)
        # Provide no-op stand-ins for the symbols IRC source imports.
        stub.SocketIO = object
        stub.join_room = lambda *a, **kw: None
        stub.leave_room = lambda *a, **kw: None
        sys.modules[name] = stub


_stub_module("flask_socketio")
