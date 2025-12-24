"""Package entry point for `python -m cwa_book_downloader`."""

from cwa_book_downloader.main import app, socketio
from cwa_book_downloader.config.env import FLASK_HOST, FLASK_PORT
from cwa_book_downloader.core.config import config

if __name__ == "__main__":
    socketio.run(app, host=FLASK_HOST, port=FLASK_PORT, debug=config.get("DEBUG", False))
