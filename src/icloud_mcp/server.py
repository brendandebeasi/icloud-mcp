"""FastMCP server exposing iCloud Calendar, Contacts and Mail as MCP tools."""

import argparse
import logging
import os
from typing import Annotated, Any

import requests
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.utilities.types import File, Image
from pydantic import Field

from . import __version__, calendar, contacts
from . import mail as mail_module
from .auth import AuthenticationError
from .config import config, configure_logging

logger = logging.getLogger(__name__)

INSTRUCTIONS = f"""iCloud MCP server v{__version__}: Calendar (CalDAV), Contacts (CardDAV) and Mail (IMAP/SMTP) for one iCloud account per request.

Conventions:
- Calendar and contact IDs are full URLs returned by the list/search tools; pass them back verbatim.
- Email message IDs are IMAP UIDs and are only valid within the folder they came from; always pass the same `folder`.
- Times: pass ISO 8601 without offset ("2025-06-01T10:00:00") together with an IANA `timezone`, or a plain date ("2025-06-01") for all-day events. Server default timezone: {config.DEFAULT_TIMEZONE}.
- Reminders lists appear as calendars with read_only=true; events cannot be created there.
- Recurring events are expanded by calendar_list_events: each occurrence is a separate entry sharing the series id; update/delete affect the whole series.
- Email bodies are converted to readable text and truncated to {config.EMAIL_BODY_MAX_CHARS} characters; use email_get_message with full_html=true for the raw HTML.
- Sending mail, creating/updating/deleting events and contacts are irreversible: confirm the details with the user first when in doubt.
"""

mcp = FastMCP(
    "iCloud",
    instructions=INSTRUCTIONS,
    version=__version__,
    website_url="https://github.com/mike-tih/icloud-mcp",
)

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

AUTH_HINT = (
    "iCloud rejected the credentials. Make sure you use an app-specific password "
    "(https://account.apple.com/account/manage), not the Apple ID password, and that "
    "the email is the Apple ID login."
)


def _run(fn, *args, **kwargs):
    """Execute an operation and translate failures into MCP tool errors."""
    try:
        return fn(*args, **kwargs)
    except ToolError:
        raise
    except AuthenticationError as e:
        raise ToolError(f"Authentication required: {e}") from e
    except (PermissionError, FileNotFoundError, ValueError) as e:
        raise ToolError(str(e)) from e
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (401, 403):
            raise ToolError(f"HTTP {status}: {AUTH_HINT}") from e
        raise ToolError(f"iCloud returned HTTP {status}: {e}") from e
    except Exception as e:
        name = type(e).__name__
        text = str(e)
        if "401" in text or "Unauthorized" in text or "AuthorizationError" in name:
            raise ToolError(f"{name}: {AUTH_HINT} ({text})") from e
        logger.exception("Tool failed: %s", fn.__name__)
        raise ToolError(f"{name}: {text}") from e


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    from starlette.responses import JSONResponse

    return JSONResponse(
        {"status": "ok", "service": "icloud-mcp", "version": __version__, "transport": "streamable-http"}
    )


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

CalendarId = Annotated[
    str | None,
    Field(description="Calendar URL/ID from calendar_list_calendars. Omit to use all event calendars (or the first one when creating)."),
]
DateFilter = Annotated[str | None, Field(description="Date YYYY-MM-DD or ISO datetime.")]
EventId = Annotated[str, Field(description="Event URL/ID as returned by calendar_list_events or calendar_search_events.")]
Timezone = Annotated[
    str | None,
    Field(description=f"IANA timezone for naive start/end values, e.g. 'Europe/Berlin' or 'UTC'. Default: {config.DEFAULT_TIMEZONE}."),
]
Rrule = Annotated[
    str | None,
    Field(
        description=(
            "RFC 5545 recurrence rule for a repeating event, e.g. 'FREQ=WEEKLY;BYDAY=TU', "
            "'FREQ=WEEKLY;INTERVAL=2;BYDAY=MO', 'FREQ=MONTHLY;BYMONTHDAY=15;COUNT=6'. "
            "Do not include DTSTART (the event start is the series start)."
        )
    ),
]
Attendees = Annotated[list[str] | None, Field(description="Attendee email addresses. Each receives an iTIP invitation by email.")]


@mcp.tool(annotations=READ_ONLY)
def calendar_list_calendars() -> list[dict[str, Any]]:
    """List the account's calendars with their IDs (URLs).

    Reminders lists are included with read_only=true; they cannot hold events.
    """
    return _run(calendar.list_calendars)


@mcp.tool(annotations=READ_ONLY)
def calendar_list_events(
    calendar_id: CalendarId = None,
    start_date: Annotated[str | None, Field(description="Range start (YYYY-MM-DD or ISO datetime). Default: 90 days ago.")] = None,
    end_date: Annotated[str | None, Field(description="Range end, inclusive for dates (YYYY-MM-DD or ISO datetime). Default: 365 days ahead.")] = None,
) -> list[dict[str, Any]]:
    """List events in a date range, sorted by start.

    Each entry has id/url, summary, description, location, start, end (ISO 8601 with offset,
    or a date for all_day events), start_timezone, recurring, rrule, attendees and calendar.
    Recurring series are expanded into one entry per occurrence; all occurrences share the
    series id, so update/delete apply to the whole series.
    """
    return _run(calendar.list_events, calendar_id, start_date, end_date)


@mcp.tool(annotations=READ_ONLY)
def calendar_search_events(
    query: Annotated[str, Field(description="Case-insensitive text matched against summary, description and location.")],
    calendar_id: CalendarId = None,
    start_date: DateFilter = None,
    end_date: DateFilter = None,
) -> list[dict[str, Any]]:
    """Search events by text within an optional date range (same output as calendar_list_events)."""
    return _run(calendar.search_events, query, calendar_id, start_date, end_date)


@mcp.tool(annotations=WRITE)
def calendar_create_event(
    summary: Annotated[str, Field(description="Event title.")],
    start: Annotated[str, Field(description="Start: 'YYYY-MM-DDTHH:MM:SS' (interpreted in `timezone`) or 'YYYY-MM-DD' for an all-day event.")],
    end: Annotated[str, Field(description="End, same format as start. For all-day events the end date is inclusive.")],
    description: Annotated[str | None, Field(description="Event notes.")] = None,
    location: Annotated[str | None, Field(description="Event location.")] = None,
    attendees: Attendees = None,
    calendar_id: CalendarId = None,
    timezone: Timezone = None,
    rrule: Rrule = None,
) -> dict[str, Any]:
    """Create a calendar event (optionally recurring) and email iTIP invitations to attendees.

    Returns the created event including its id/url. If some invitations could not be sent,
    `invitations_failed` lists those addresses.
    """
    return _run(
        calendar.create_event,
        summary, start, end, description, location, attendees, calendar_id, timezone, rrule,
    )


@mcp.tool(annotations=DESTRUCTIVE)
def calendar_update_event(
    event_id: EventId,
    summary: Annotated[str | None, Field(description="New title.")] = None,
    start: Annotated[str | None, Field(description="New start ('YYYY-MM-DDTHH:MM:SS' or 'YYYY-MM-DD').")] = None,
    end: Annotated[str | None, Field(description="New end, same format as start.")] = None,
    description: Annotated[str | None, Field(description="New notes (empty string clears).")] = None,
    location: Annotated[str | None, Field(description="New location (empty string clears).")] = None,
    attendees: Annotated[list[str] | None, Field(description="New attendee list; replaces the existing one and sends updated invitations.")] = None,
    timezone: Annotated[str | None, Field(description="IANA timezone for the new start/end. Omit to keep the event's current timezone.")] = None,
    rrule: Annotated[str | None, Field(description="New recurrence rule for the whole series (e.g. 'FREQ=WEEKLY;BYDAY=TU'). Pass '' to make the event non-recurring.")] = None,
) -> dict[str, Any]:
    """Update fields of an existing event. Only provided fields change; recurring series are updated as a whole."""
    return _run(
        calendar.update_event,
        event_id, summary, start, end, description, location, attendees, timezone, rrule,
    )


@mcp.tool(annotations=DESTRUCTIVE)
def calendar_delete_event(event_id: EventId) -> dict[str, Any]:
    """Delete an event (the whole series for recurring events) and email cancellations to its attendees."""
    return _run(calendar.delete_event, event_id)


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

ContactId = Annotated[str, Field(description="Contact URL/ID from contacts_list or contacts_search.")]
Phones = Annotated[list[str] | None, Field(description="Phone numbers.")]
Emails = Annotated[list[str] | None, Field(description="Email addresses.")]
Addresses = Annotated[list[str] | None, Field(description="Postal addresses, one string each.")]


@mcp.tool(annotations=READ_ONLY)
def contacts_list(
    limit: Annotated[int | None, Field(description="Maximum number of contacts to return. Default: all.", ge=1)] = None,
) -> list[dict[str, Any]]:
    """List contacts from the default address book: id/url, name, phones, emails, addresses, organization, title."""
    return _run(contacts.list_contacts, limit)


@mcp.tool(annotations=READ_ONLY)
def contacts_search(
    query: Annotated[str, Field(description="Text matched against name, organization, emails and phone digits.")],
    limit: Annotated[int | None, Field(description="Maximum number of matches.", ge=1)] = None,
) -> list[dict[str, Any]]:
    """Find contacts by name, organization, email or phone number."""
    return _run(contacts.search_contacts, query, limit)


@mcp.tool(annotations=READ_ONLY)
def contacts_get(contact_id: ContactId) -> dict[str, Any]:
    """Get one contact with all fields."""
    return _run(contacts.get_contact, contact_id)


@mcp.tool(annotations=WRITE)
def contacts_create(
    name: Annotated[str, Field(description="Full name, e.g. 'Jane Doe'.")],
    phones: Phones = None,
    emails: Emails = None,
    addresses: Addresses = None,
    organization: Annotated[str | None, Field(description="Company / organization.")] = None,
    title: Annotated[str | None, Field(description="Job title.")] = None,
) -> dict[str, Any]:
    """Create a contact in the default address book and return it with its id/url."""
    return _run(contacts.create_contact, name, phones, emails, addresses, organization, title)


@mcp.tool(annotations=DESTRUCTIVE)
def contacts_update(
    contact_id: ContactId,
    name: Annotated[str | None, Field(description="New full name.")] = None,
    phones: Annotated[list[str] | None, Field(description="New phone list (replaces existing).")] = None,
    emails: Annotated[list[str] | None, Field(description="New email list (replaces existing).")] = None,
    addresses: Annotated[list[str] | None, Field(description="New address list (replaces existing).")] = None,
    organization: Annotated[str | None, Field(description="New organization.")] = None,
    title: Annotated[str | None, Field(description="New job title.")] = None,
) -> dict[str, Any]:
    """Update a contact. Only provided fields change; list fields replace the existing values."""
    return _run(contacts.update_contact, contact_id, name, phones, emails, addresses, organization, title)


@mcp.tool(annotations=DESTRUCTIVE)
def contacts_delete(contact_id: ContactId) -> dict[str, str]:
    """Delete a contact permanently."""
    return _run(contacts.delete_contact, contact_id)


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------

Folder = Annotated[
    str,
    Field(description="IMAP folder name. iCloud folders: INBOX, Drafts, 'Sent Messages', 'Deleted Messages', Junk, Archive (see email_list_folders)."),
]
MessageId = Annotated[str, Field(description="Message ID (IMAP UID) from email_list_messages/email_search, valid within `folder` only.")]
Limit = Annotated[int, Field(description="Maximum number of messages, newest first.", ge=1, le=500)]
IncludeBody = Annotated[bool, Field(description="Include body_text (readable text, truncated). Set false for a fast header-only listing.")]


@mcp.tool(annotations=READ_ONLY)
def email_list_folders() -> list[dict[str, Any]]:
    """List mail folders with their IMAP flags (e.g. \\Sent, \\Trash)."""
    return _run(mail_module.list_folders)


@mcp.tool(annotations=READ_ONLY)
def email_list_messages(
    folder: Folder = "INBOX",
    limit: Limit = 50,
    unread_only: Annotated[bool, Field(description="Only unread messages.")] = False,
    include_body: IncludeBody = True,
) -> list[dict[str, Any]]:
    """List the newest messages in a folder: id, subject, from, to, date, flags, unread, has_attachments and body_text."""
    return _run(mail_module.list_messages, folder, limit, unread_only, include_body)


@mcp.tool(annotations=READ_ONLY)
def email_search(
    query: Annotated[str | None, Field(description="Text matched against Subject OR From.")] = None,
    sender: Annotated[str | None, Field(description="Text/address matched against From (full addresses work best).")] = None,
    recipient: Annotated[str | None, Field(description="Text/address matched against To or Cc.")] = None,
    subject: Annotated[str | None, Field(description="Text matched against Subject.")] = None,
    body: Annotated[str | None, Field(description="Text matched against the message body (slower).")] = None,
    since: Annotated[str | None, Field(description="Only messages on/after this date, YYYY-MM-DD.")] = None,
    before: Annotated[str | None, Field(description="Only messages before this date, YYYY-MM-DD.")] = None,
    unread_only: Annotated[bool, Field(description="Only unread messages.")] = False,
    folder: Folder = "INBOX",
    limit: Limit = 50,
    include_body: IncludeBody = True,
) -> list[dict[str, Any]]:
    """Server-side IMAP search. All provided filters are combined with AND; at least one is required.

    Returns the same fields as email_list_messages, newest first.
    """
    return _run(
        mail_module.search_messages,
        query, sender, recipient, subject, body, since, before, unread_only, folder, limit, include_body,
    )


@mcp.tool(annotations=READ_ONLY)
def email_get_message(
    message_id: MessageId,
    folder: Folder = "INBOX",
    include_body: Annotated[bool, Field(description="Include the body.")] = True,
    full_html: Annotated[bool, Field(description="Also return body_html (raw HTML, size-capped).")] = False,
) -> dict[str, Any]:
    """Get one message: headers (from, to, cc, reply_to, date, Message-ID), flags, body_text and the attachments list.

    Attachments are described by index, name, mime_type and size; download one with email_get_attachment.
    """
    return _run(mail_module.get_message, message_id, folder, include_body, full_html)


@mcp.tool(annotations=READ_ONLY)
def email_get_messages(
    message_ids: Annotated[list[str], Field(description="Message IDs (IMAP UIDs) from the same folder.", min_length=1)],
    folder: Folder = "INBOX",
    include_body: Annotated[bool, Field(description="Include bodies.")] = True,
    full_html: Annotated[bool, Field(description="Also return body_html.")] = False,
) -> list[dict[str, Any]]:
    """Fetch several messages in one round trip (same fields as email_get_message). Missing IDs yield an error entry."""
    return _run(mail_module.get_messages, message_ids, folder, include_body, full_html)


@mcp.tool(annotations=READ_ONLY, output_schema=None)
def email_get_attachment(
    message_id: MessageId,
    attachment: Annotated[str, Field(description="Attachment index ('1') or (partial) file name as listed by email_get_message.")],
    folder: Folder = "INBOX",
    save_dir: Annotated[
        str | None,
        Field(description="Directory on the machine running this server to save the file into. Recommended when running locally (stdio). When omitted the file content is returned inline."),
    ] = None,
) -> list:
    """Download an email attachment.

    With save_dir the file is written to disk and its path returned; otherwise the file is
    returned inline (images as image content, other types as an embedded resource).
    """
    result = _run(mail_module.get_attachment, message_id, attachment, folder, save_dir)
    data = result.pop("data", None)
    if data is None:
        return [result]

    mime = result["mime_type"]
    name = result["name"]
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    summary = {k: v for k, v in result.items()}
    if mime.startswith("image/"):
        fmt = ext or mime.split("/", 1)[1]
        return [summary, Image(data=data, format="jpeg" if fmt == "jpg" else fmt)]
    return [summary, File(data=data, format=mime.split("/", 1)[1] if "/" in mime else None, name=name)]


@mcp.tool(annotations=WRITE)
def email_send(
    to: Annotated[str, Field(description="Recipient address(es), comma-separated.")],
    subject: Annotated[str, Field(description="Subject line.")],
    body: Annotated[str, Field(description="Message body (plain text, or HTML when html=true).")],
    cc: Annotated[str | None, Field(description="CC address(es), comma-separated.")] = None,
    bcc: Annotated[str | None, Field(description="BCC address(es), comma-separated.")] = None,
    html: Annotated[bool, Field(description="Treat body as HTML (a plain-text alternative is generated automatically).")] = False,
    attachment_paths: Annotated[
        list[str] | None,
        Field(description="Local file paths (on the machine running this server) to attach. Only available when local file access is enabled (default for stdio)."),
    ] = None,
    reply_to_message_id: Annotated[str | None, Field(description="UID of a message to reply to; sets In-Reply-To/References so mail clients thread the reply.")] = None,
    reply_to_folder: Annotated[str, Field(description="Folder of reply_to_message_id.")] = "INBOX",
) -> dict[str, Any]:
    """Send an email from the account (SMTP) and store a copy in the Sent folder."""
    return _run(
        mail_module.send_message,
        to, subject, body, cc, bcc, html, attachment_paths, reply_to_message_id, reply_to_folder,
    )


@mcp.tool(annotations=DESTRUCTIVE)
def email_move(
    message_id: MessageId,
    from_folder: Annotated[str, Field(description="Current folder of the message.")],
    to_folder: Annotated[str, Field(description="Destination folder (must exist; see email_list_folders).")],
) -> dict[str, str]:
    """Move a message to another folder. The message gets a new UID in the destination folder."""
    return _run(mail_module.move_message, message_id, from_folder, to_folder)


@mcp.tool(annotations=DESTRUCTIVE)
def email_delete(
    message_id: MessageId,
    folder: Folder = "INBOX",
    permanent: Annotated[bool, Field(description="True: delete permanently. False (default): move to the Trash folder ('Deleted Messages' on iCloud).")] = False,
) -> dict[str, str]:
    """Move a message to Trash, or delete it permanently."""
    return _run(mail_module.delete_message, message_id, folder, permanent)


@mcp.tool(annotations=WRITE)
def email_mark_read(message_id: MessageId, folder: Folder = "INBOX") -> dict[str, str]:
    """Mark a message as read (set \\Seen)."""
    return _run(mail_module.mark_as_read, message_id, folder)


@mcp.tool(annotations=WRITE)
def email_mark_unread(message_id: MessageId, folder: Folder = "INBOX") -> dict[str, str]:
    """Mark a message as unread (clear \\Seen)."""
    return _run(mail_module.mark_as_unread, message_id, folder)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="icloud-mcp",
        description="iCloud MCP server (Calendar, Contacts, Mail). Default transport: stdio.",
    )
    parser.add_argument("--http", action="store_true", help="Serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default=config.MCP_SERVER_HOST, help="HTTP bind host (default: %(default)s)")
    parser.add_argument("--port", type=int, default=config.MCP_SERVER_PORT, help="HTTP port (default: %(default)s, env PORT)")
    parser.add_argument("--path", default=config.MCP_SERVER_PATH, help="HTTP endpoint path (default: %(default)s)")
    parser.add_argument(
        "--stateful",
        action="store_true",
        help="Keep MCP sessions in memory (default: stateless, safe behind load balancers)",
    )
    parser.add_argument("--log-level", default=config.LOG_LEVEL, help="Logging level (default: %(default)s)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    configure_logging(args.log_level)

    use_http = args.http or os.getenv("MCP_TRANSPORT", "").lower() in {"http", "streamable-http"}

    # Local file access (attachments on disk) is safe when the server runs on the
    # user's own machine, and off by default for a shared HTTP deployment.
    if config.LOCAL_FILES is None:
        config.LOCAL_FILES = not use_http

    if use_http:
        logger.info(
            "Starting iCloud MCP %s on http://%s:%s%s (stateless=%s, local_files=%s)",
            __version__, args.host, args.port, args.path, not args.stateful, config.LOCAL_FILES,
        )
        mcp.run(
            transport="http",
            host=args.host,
            port=args.port,
            path=args.path,
            stateless_http=not args.stateful,
            show_banner=False,
            log_level=args.log_level.lower(),
        )
    else:
        logger.info("Starting iCloud MCP %s on stdio (local_files=%s)", __version__, config.LOCAL_FILES)
        mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
