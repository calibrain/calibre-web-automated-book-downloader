"""Pre-download release inspection: list a release's files and plan a multi-book split."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import jsonify, request

from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import is_audiobook
from shelfmark.download.postprocess.packs import PackFile, PackPlan, plan_pack
from shelfmark.download.postprocess.policy import (
    get_supported_audiobook_formats,
    get_supported_formats,
)
from shelfmark.release_sources import get_handler

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask
    from werkzeug.wrappers import Response

logger = setup_logger(__name__)

_INSPECT_ERRORS = (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError)
NOT_INSPECTABLE_REASON = "This source cannot list the release's files before downloading"


def _serialize_plan(plan: PackPlan) -> dict[str, Any]:
    return {
        "is_pack": plan.is_pack,
        "ignored": plan.ignored,
        "books": [
            {
                "title": book.title,
                "series_position": book.series_position,
                "year": book.year,
                "files": book.files,
            }
            for book in plan.books
        ],
    }


def inspect_release(data: dict[str, Any]) -> dict[str, Any]:
    """Build the inspect response for a release payload (same shape as a download)."""
    source = str(data["source"])
    handler = get_handler(source)
    try:
        files: list[PackFile] | None = handler.list_files(data)
    except _INSPECT_ERRORS as exc:
        logger.warning(
            "Could not list files for %s release %s: %s", source, data.get("source_id"), exc
        )
        return {"inspected": False, "reason": str(exc), "files": [], "plan": None}

    if files is None:
        return {"inspected": False, "reason": NOT_INSPECTABLE_REASON, "files": [], "plan": None}

    content_type = data.get("content_type")
    supported = (
        get_supported_audiobook_formats()
        if is_audiobook(content_type if isinstance(content_type, str) else None)
        else get_supported_formats()
    )
    series_name = data.get("series_name")
    author_name = data.get("author")
    plan = plan_pack(
        files,
        supported_extensions=set(supported),
        series_name=series_name if isinstance(series_name, str) else None,
        author_name=author_name if isinstance(author_name, str) else None,
    )
    return {
        "inspected": True,
        "reason": None,
        "files": [{"path": f.path, "size": f.size} for f in files],
        "plan": _serialize_plan(plan),
    }


def register_release_inspect_routes(
    app: Flask,
    login_required: Callable[..., Any],
    *,
    policy_gate: Callable[[dict[str, Any]], tuple[Response, int] | None] | None = None,
) -> None:
    """Register POST /api/releases/inspect.

    `policy_gate` is given the validated payload and returns the refusal response
    for a user whose request policy does not let them see the release, or None.
    Listing a release's files reaches the source through shared credentials, so
    the gate runs before the handler is asked anything.
    """

    @app.route("/api/releases/inspect", methods=["POST"])
    @login_required
    def api_inspect_release() -> Response | tuple[Response, int]:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "No data provided"}), 400
        if not data.get("source_id"):
            return jsonify({"error": "source_id is required"}), 400
        if not data.get("source"):
            return jsonify({"error": "source is required"}), 400
        try:
            get_handler(str(data["source"]))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if policy_gate is not None:
            refusal = policy_gate(data)
            if refusal is not None:
                return refusal
        return jsonify(inspect_release(data))
