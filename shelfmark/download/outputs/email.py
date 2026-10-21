"""Email output integration for delivering completed downloads as attachments."""

from __future__ import annotations

import mimetypes
import smtplib
import ssl
from contextlib import suppress
from dataclasses import dataclass, replace
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import TYPE_CHECKING, Any

import shelfmark.core.config as core_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.naming import derive_primary_title
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.outputs import register_output
from shelfmark.download.staging import (
    STAGE_COPY,
    STAGE_MOVE,
    STAGE_NONE,
    build_staging_dir,
    get_staging_dir,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

EMAIL_OUTPUT_MODE = "email"

SECURITY_NONE = "none"
SECURITY_STARTTLS = "starttls"
SECURITY_SSL = "ssl"
ALLOWED_SECURITY = {SECURITY_NONE, SECURITY_STARTTLS, SECURITY_SSL}


class EmailOutputError(Exception):
    """Raised when the email output integration fails."""


@dataclass(frozen=True)
class EmailSmtpConfig:
    """SMTP connection settings for the email output."""

    host: str
    port: int
    security: str
    username: str = ""
    password: str = ""
    from_addr: str = ""
    timeout_seconds: int = 60
    allow_unverified_tls: bool = False
    subject_template: str = "{Title}"


def _parse_int(value: Any, label: str, *, minimum: int = 1) -> int:
    if value is None or value == "":
        msg = f"{label} is required"
        raise EmailOutputError(msg)
    if not isinstance(value, (int, float, str)):
        msg = f"{label} must be a number"
        raise EmailOutputError(msg)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        msg = f"{label} must be a number"
        raise EmailOutputError(msg) from exc
    if parsed < minimum:
        msg = f"{label} must be >= {minimum}"
        raise EmailOutputError(msg)
    return parsed


def build_email_smtp_config(values: Mapping[str, Any]) -> EmailSmtpConfig:
    """Build and validate SMTP settings for the email output."""
    host = str(values.get("EMAIL_SMTP_HOST", "") or "").strip()
    port = _parse_int(values.get("EMAIL_SMTP_PORT", 587), "SMTP port", minimum=1)

    security = str(values.get("EMAIL_SMTP_SECURITY", SECURITY_STARTTLS) or "").strip().lower()
    if security not in ALLOWED_SECURITY:
        msg = f"SMTP security must be one of: {', '.join(sorted(ALLOWED_SECURITY))}"
        raise EmailOutputError(msg)

    username = str(values.get("EMAIL_SMTP_USERNAME", "") or "").strip()
    password = values.get("EMAIL_SMTP_PASSWORD", "") or ""

    from_addr = str(values.get("EMAIL_FROM", "") or "").strip()
    subject_template = str(values.get("EMAIL_SUBJECT_TEMPLATE", "{Title}") or "").strip()
    timeout_seconds = _parse_int(
        values.get("EMAIL_SMTP_TIMEOUT_SECONDS", 60), "SMTP timeout (seconds)", minimum=1
    )
    allow_unverified_tls = bool(values.get("EMAIL_ALLOW_UNVERIFIED_TLS", False))

    if not host:
        msg = "SMTP host is required"
        raise EmailOutputError(msg)
    if username and not password:
        msg = "SMTP password is required when username is set"
        raise EmailOutputError(msg)

    if not from_addr:
        # If From is not configured, fall back to the SMTP username if it is an email address.
        username_email = parseaddr(username)[1]
        if username_email and "@" in username_email:
            from_addr = f"Shelfmark <{username_email}>"
        else:
            msg = "From address is required (or set SMTP username to an email address)."
            raise EmailOutputError(msg)

    return EmailSmtpConfig(
        host=host,
        port=port,
        security=security,
        username=username,
        password=password,
        from_addr=from_addr,
        timeout_seconds=timeout_seconds,
        allow_unverified_tls=allow_unverified_tls,
        subject_template=subject_template or "{Title}",
    )


def _get_email_settings() -> dict[str, Any]:
    return {
        "EMAIL_SMTP_HOST": core_config.config.get("EMAIL_SMTP_HOST", ""),
        "EMAIL_SMTP_PORT": core_config.config.get("EMAIL_SMTP_PORT", 587),
        "EMAIL_SMTP_SECURITY": core_config.config.get("EMAIL_SMTP_SECURITY", SECURITY_STARTTLS),
        "EMAIL_SMTP_USERNAME": core_config.config.get("EMAIL_SMTP_USERNAME", ""),
        "EMAIL_SMTP_PASSWORD": core_config.config.get("EMAIL_SMTP_PASSWORD", ""),
        "EMAIL_FROM": core_config.config.get("EMAIL_FROM", ""),
        "EMAIL_SUBJECT_TEMPLATE": core_config.config.get("EMAIL_SUBJECT_TEMPLATE", "{Title}"),
        "EMAIL_SMTP_TIMEOUT_SECONDS": core_config.config.get("EMAIL_SMTP_TIMEOUT_SECONDS", 60),
        "EMAIL_ALLOW_UNVERIFIED_TLS": core_config.config.get("EMAIL_ALLOW_UNVERIFIED_TLS", False),
    }


def _parse_attachment_limit_mb(value: object) -> int:
    if not isinstance(value, (int, float, str)):
        return 25
    try:
        return int(value)
    except TypeError, ValueError:
        return 25


def _render_subject(template: str, task: DownloadTask) -> str:
    primary_title = derive_primary_title(task.title, task.subtitle)
    mapping = {
        "Author": task.author or "",
        "Title": task.title or "",
        "PrimaryTitle": primary_title,
        "Year": task.year or "",
        "Series": task.series_name or "",
        "SeriesPosition": task.series_position or "",
        "Subtitle": task.subtitle or "",
        "Format": task.format or "",
    }
    try:
        rendered = template.format(**mapping)
    except IndexError, KeyError, ValueError:
        rendered = template

    rendered = " ".join(str(rendered).split()).strip()
    return rendered or "Shelfmark"


def _msgid_domain(from_addr: str) -> str:
    from_email = parseaddr(from_addr)[1]
    domain = (from_email.partition("@")[2] or "").strip().rstrip(">")
    return domain or "shelfmark.local"


def compose_email_message(
    smtp_config: EmailSmtpConfig,
    *,
    task: DownloadTask,
    recipient: str,
    files: list[Path],
) -> EmailMessage:
    """Compose the outbound email message for a completed download."""
    message = EmailMessage()
    message["From"] = smtp_config.from_addr
    message["To"] = recipient
    message["Subject"] = _render_subject(smtp_config.subject_template, task)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=_msgid_domain(smtp_config.from_addr))

    # Keep email body empty; attachments carry the content.
    message.set_content("")

    for file_path in files:
        filename = file_path.name
        data = file_path.read_bytes()

        content_type, encoding = mimetypes.guess_type(filename)
        if content_type is None or encoding is not None:
            content_type = "application/octet-stream"

        main_type, sub_type = content_type.split("/", 1)
        message.add_attachment(data, maintype=main_type, subtype=sub_type, filename=filename)

    return message


def _create_tls_context(*, allow_unverified: bool) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if allow_unverified:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def test_smtp_connection(smtp_config: EmailSmtpConfig) -> None:
    """Connect and (optionally) authenticate to the SMTP server. Does not send mail."""
    smtp: smtplib.SMTP | None = None
    try:
        if smtp_config.security == SECURITY_SSL:
            context = _create_tls_context(allow_unverified=smtp_config.allow_unverified_tls)
            smtp = smtplib.SMTP_SSL(
                smtp_config.host,
                smtp_config.port,
                timeout=smtp_config.timeout_seconds,
                context=context,
            )
        else:
            smtp = smtplib.SMTP(
                smtp_config.host, smtp_config.port, timeout=smtp_config.timeout_seconds
            )

        smtp.ehlo()

        if smtp_config.security == SECURITY_STARTTLS:
            context = _create_tls_context(allow_unverified=smtp_config.allow_unverified_tls)
            smtp.starttls(context=context)
            smtp.ehlo()

        if smtp_config.username:
            smtp.login(smtp_config.username, smtp_config.password)
    except smtplib.SMTPAuthenticationError as exc:
        msg = "SMTP authentication failed"
        raise EmailOutputError(msg) from exc
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError, OSError) as exc:
        msg = f"Could not connect to SMTP server: {exc}"
        raise EmailOutputError(msg) from exc
    finally:
        if smtp is not None:
            with suppress(Exception):
                smtp.quit()
            with suppress(Exception):
                smtp.close()


def send_email_message(smtp_config: EmailSmtpConfig, message: EmailMessage) -> None:
    """Send a prepared email message using the configured SMTP transport."""
    smtp: smtplib.SMTP | None = None
    try:
        if smtp_config.security == SECURITY_SSL:
            context = _create_tls_context(allow_unverified=smtp_config.allow_unverified_tls)
            smtp = smtplib.SMTP_SSL(
                smtp_config.host,
                smtp_config.port,
                timeout=smtp_config.timeout_seconds,
                context=context,
            )
        else:
            smtp = smtplib.SMTP(
                smtp_config.host, smtp_config.port, timeout=smtp_config.timeout_seconds
            )

        smtp.ehlo()

        if smtp_config.security == SECURITY_STARTTLS:
            context = _create_tls_context(allow_unverified=smtp_config.allow_unverified_tls)
            smtp.starttls(context=context)
            smtp.ehlo()

        if smtp_config.username:
            smtp.login(smtp_config.username, smtp_config.password)

        smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        msg = "SMTP authentication failed"
        raise EmailOutputError(msg) from exc
    except (smtplib.SMTPException, TimeoutError, OSError) as exc:
        msg = f"Failed to send email: {exc}"
        raise EmailOutputError(msg) from exc
    finally:
        if smtp is not None:
            with suppress(Exception):
                smtp.quit()
            with suppress(Exception):
                smtp.close()


def _supports_email(task: DownloadTask) -> bool:
    return not check_audiobook(task.content_type)


def _post_process_email(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback: Callable[[str, str | None], None],
    *,
    preserve_source_on_failure: bool = False,
) -> str | None:
    from shelfmark.download.postprocess.packs import drop_ignored_files
    from shelfmark.download.postprocess.pipeline import (
        CustomScriptContext,
        OutputPlan,
        cleanup_output_staging,
        is_managed_workspace_path,
        maybe_run_custom_script,
        prepare_output_files,
        safe_cleanup_path,
    )

    if cancel_flag.is_set():
        logger.info("Task %s: cancelled before email send", task.task_id)
        return None

    try:
        smtp_config = build_email_smtp_config(_get_email_settings())
    except EmailOutputError as exc:
        logger.warning("Task %s: email configuration error: %s", task.task_id, exc)
        status_callback("error", str(exc))
        return None

    output_args = task.output_args or {}
    if not isinstance(output_args, dict):
        output_args = {}

    recipient = str(output_args.get("to", "") or "").strip()
    label = str(output_args.get("label", "") or "").strip() or recipient
    if not recipient:
        status_callback(
            "error",
            "No email recipient configured. Set a per-user email recipient or a default in Downloads -> Books.",
        )
        return None

    status_callback("resolving", "Preparing email")

    stage_action = STAGE_NONE
    if is_managed_workspace_path(temp_file):
        stage_action = STAGE_COPY if preserve_source_on_failure else STAGE_MOVE
    staging_dir = (
        build_staging_dir("email", task.task_id)
        if stage_action != STAGE_NONE
        else get_staging_dir()
    )

    output_plan = OutputPlan(
        mode=EMAIL_OUTPUT_MODE,
        stage_action=stage_action,
        staging_dir=staging_dir,
        allow_archive_extraction=True,
    )

    prepared = prepare_output_files(
        temp_file,
        task,
        EMAIL_OUTPUT_MODE,
        status_callback,
        output_plan=output_plan,
        preserve_source_on_failure=preserve_source_on_failure,
    )
    if not prepared:
        return None
    # Files the user skipped in the pack review are not sent.
    prepared = replace(prepared, files=drop_ignored_files(prepared.files, task.ignored_files))

    success = False
    try:
        if not prepared.files:
            status_callback("error", "No book files found")
            return None

        limit_mb_raw = core_config.config.get("EMAIL_ATTACHMENT_SIZE_LIMIT_MB", 25)
        attachment_limit_mb = _parse_attachment_limit_mb(limit_mb_raw)

        if attachment_limit_mb > 0:
            limit_bytes = attachment_limit_mb * 1024 * 1024
            file_sizes: list[tuple[Path, int]] = []
            total_bytes = 0

            for file_path in prepared.files:
                try:
                    size_bytes = file_path.stat().st_size
                except OSError:
                    continue
                file_sizes.append((file_path, size_bytes))
                total_bytes += size_bytes

            too_large = [(path, size) for path, size in file_sizes if size > limit_bytes]
            if too_large:
                path, size = max(too_large, key=lambda item: item[1])
                status_callback(
                    "error",
                    f"Attachment '{path.name}' is {size / (1024 * 1024):.1f} MB (limit {attachment_limit_mb} MB)",
                )
                return None

            # Most providers enforce a message size limit and attachments are base64-encoded (~33% overhead).
            estimated_encoded_bytes = int(total_bytes * 4 / 3)
            if estimated_encoded_bytes > limit_bytes:
                status_callback(
                    "error",
                    (
                        f"Attachments total {total_bytes / (1024 * 1024):.1f} MB "
                        f"(estimated encoded {estimated_encoded_bytes / (1024 * 1024):.1f} MB) "
                        f"exceeds limit {attachment_limit_mb} MB"
                    ),
                )
                return None

        if cancel_flag.is_set():
            logger.info("Task %s: cancelled before email send", task.task_id)
            return None

        status_callback("resolving", f"Sending email to {label}")
        message = compose_email_message(
            smtp_config,
            task=task,
            recipient=recipient,
            files=prepared.files,
        )
        send_email_message(smtp_config, message)

        script_context = CustomScriptContext(
            task=task,
            phase="post_email",
            output_mode=EMAIL_OUTPUT_MODE,
            destination=prepared.files[0].parent if prepared.files else None,
            final_paths=prepared.files,
            output_details={
                "email": {
                    "to": recipient,
                    "label": label,
                    "host": smtp_config.host,
                    "port": smtp_config.port,
                    "security": smtp_config.security,
                }
            },
        )
        if not maybe_run_custom_script(script_context, status_callback=status_callback):
            return None

        status_callback("complete", f"Sent to {label}")
        success = True
        output_path = f"email://{task.task_id}"

    except EmailOutputError as exc:
        logger.warning("Task %s: email send failed: %s", task.task_id, exc)
        status_callback("error", str(exc))
        return None
    except (OSError, TypeError, ValueError) as exc:
        logger.error_trace("Task %s: unexpected error sending email: %s", task.task_id, exc)
        status_callback("error", f"Email send failed: {exc}")
        return None
    else:
        return output_path
    finally:
        cleanup_output_staging(
            prepared.output_plan,
            prepared.working_path,
            task,
            prepared.cleanup_paths,
        )
        if preserve_source_on_failure and success:
            safe_cleanup_path(temp_file, task)


@register_output(EMAIL_OUTPUT_MODE, supports_task=_supports_email, priority=10)
def process_email_output(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback: Callable[[str, str | None], None],
    *,
    preserve_source_on_failure: bool = False,
) -> str | None:
    """Process a completed download through the email output."""
    return _post_process_email(
        temp_file,
        task,
        cancel_flag,
        status_callback,
        preserve_source_on_failure=preserve_source_on_failure,
    )
