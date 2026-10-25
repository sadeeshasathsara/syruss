"""
Email: each approved record is sent to a few addresses as a short summary with the record attached (JSON, CSV or both). It goes out through
the mail service Syruss is set up with, so nothing needs configuring here beyond who receives it. At most five addresses, and the message text is
fixed, so this can't be turned into a way to send other mail.
"""
import csv
import html
import io
import json
import re
from typing import Any, Dict, List, Optional
from app.services.destinations.base import Adapter, Field, SendContext, SendResult, TestResult, register
from app.services.notifications.transport import EmailError, EmailMessage, send

ADDRESS = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]{2,}$")
MAX_RECIPIENTS = 5


def recipients(config: Dict[str, Any]) -> List[str]:
    return [a.strip() for a in re.split(r"[,;\s]+", str(config.get("to") or "")) if a.strip()]


def csv_of(data: Dict[str, Any]) -> bytes:
    """The record in long form: one row for each value (object, which one, field, value)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["object", "entry", "field", "value"])
    for obj, rows in (data or {}).items():
        for i, row in enumerate(rows if isinstance(rows, list) else [rows], start=1):
            for field, value in (row or {}).items() if isinstance(row, dict) else [("value", row)]:
                writer.writerow([obj, i, field, json.dumps(value) if isinstance(value, (dict, list)) else value])
    return out.getvalue().encode()


def subject_for(template: str, doc: Dict[str, Any], revision: int) -> str:
    class Safe(dict):
        def __missing__(self, key: str) -> str:
            return ""
    text = (template or "Approved: {filename}").format_map(Safe(filename=doc.get("filename") or "document", external_id=doc.get("external_id") or "", revision=revision))
    return re.sub(r"[\r\n]+", " ", text)[:200]


def message_for(envelope: Dict[str, Any], config: Dict[str, Any], revision: int) -> EmailMessage:
    doc, record = envelope["document"], envelope["record"]
    flat = []
    for obj, rows in (record["data"] or {}).items():
        first = (rows[0] if isinstance(rows, list) and rows else rows) if rows else None
        if isinstance(first, dict):
            flat += [(f"{obj} › {k}", v) for k, v in first.items() if not isinstance(v, (dict, list))][:12]
    lines = [f"{doc.get('filename')} was approved" + (f" (revision {revision}, a correction)" if revision > 1 else "") + ".", ""] + [f"{k}: {v}" for k, v in flat[:30]]
    if doc.get("external_id"):
        lines.insert(1, f"Your reference: {doc['external_id']}")
    rows = "".join(f"<tr><td style='padding:2px 12px 2px 0;color:#555'>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>" for k, v in flat[:30])
    body = f"<p>{html.escape(lines[0])}</p><table>{rows}</table><p style='color:#555'>The full record is attached.</p>"
    mode = config.get("attach") or "json"
    attachments = []
    if mode in ("json", "both"):
        attachments.append((f"{re.sub(r'[^A-Za-z0-9._-]+', '_', doc.get('filename') or 'record')}.json", json.dumps(envelope, indent=2, default=str).encode(), "application/json"))
    if mode in ("csv", "both"):
        attachments.append((f"{re.sub(r'[^A-Za-z0-9._-]+', '_', doc.get('filename') or 'record')}.csv", csv_of(record["data"]), "text/csv"))
    return EmailMessage(to=", ".join(recipients(config)), subject=subject_for(config.get("subject") or "", doc, revision), text="\n".join(lines) + "\n\nThe full record is attached.",
                        html=body, attachments=attachments)


class EmailAdapter(Adapter):
    kind = "email"
    label = "Email"
    blurb = "Send each approved record to a few people by email, with the record attached."
    category = "mail"
    fields = [
        Field("to", "Send to", "text", required=True, placeholder="accounts@example.com, ap@example.com", help=f"Up to {MAX_RECIPIENTS} addresses, separated by commas."),
        Field("subject", "Subject", "text", default="Approved: {filename}", help="{filename}, {external_id} and {revision} are filled in."),
        Field("attach", "Attach the record as", "select", default="json", options=[{"value": "json", "label": "JSON"}, {"value": "csv", "label": "CSV"}, {"value": "both", "label": "Both"}]),
    ]

    def required_problems(self, config: Dict[str, Any], secrets: Dict[str, Any]) -> List[str]:
        problems = super().required_problems(config, secrets)
        found = recipients(config)
        if len(found) > MAX_RECIPIENTS:
            problems.append(f"At most {MAX_RECIPIENTS} addresses.")
        problems += [f"'{a}' is not an email address." for a in found if not ADDRESS.match(a)]
        return problems

    async def test(self, config: Dict[str, Any], secrets: Dict[str, Any], destination_id: Optional[str] = None) -> TestResult:
        try:
            await send(EmailMessage(to=", ".join(recipients(config)), subject="A test from Syruss", text="This is a test. Approved records will arrive like this, with the record attached.",
                                    html="<p>This is a test. Approved records will arrive like this, with the record attached.</p>"))
        except EmailError as exc:
            return TestResult(False, str(exc))
        return TestResult(True, f"A test message was sent to {', '.join(recipients(config))}.")

    async def send(self, ctx: SendContext) -> SendResult:
        try:
            message = message_for(ctx.envelope, ctx.config, ctx.revision)
            await send(message)
        except EmailError as exc:
            return SendResult(False, error=str(exc), retryable=True, request=f"Email to {ctx.config.get('to')}")
        return SendResult(True, 200, request=f"Email to {message.to}: {message.subject}", response=f"{len(message.attachments or [])} attachment(s)")


register(EmailAdapter())
