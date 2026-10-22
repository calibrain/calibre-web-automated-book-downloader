"""
slskd test configuration.

Stubs out optional heavy dependencies (flask_socketio) that are not available
in lightweight dev/CI environments. These are only needed by the IRC source,
which is unrelated to the slskd plugin under test.
"""

import importlib.util
import sys
import types


def _stub_module(name: str) -> None:
    """Insert a minimal stub module into sys.modules if the real one is not installed.

    A stub over an installed module would break every later test in the same worker that
    needs the real one (the Flask app imports ``flask_socketio.emit``).
    """
    if name not in sys.modules and importlib.util.find_spec(name) is None:
        stub = types.ModuleType(name)
        # Provide no-op stand-ins for the symbols IRC source imports.
        stub.SocketIO = object
        stub.join_room = lambda *a, **kw: None
        stub.leave_room = lambda *a, **kw: None
        sys.modules[name] = stub


_stub_module("flask_socketio")
