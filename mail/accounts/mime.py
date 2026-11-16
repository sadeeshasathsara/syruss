"""A message as mail programs read it: From the account, threading headers, plain text and HTML, attachments."""
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from mail.accounts.base import Outbound


def build(message: Outbound, address: str, name: str) -> EmailMessage:
    """`address` and `name`: the account's. The message may name the sender differently (Syruss Support), never its address."""
    mime = EmailMessage()
    mime["From"] = formataddr((message.sender_name or name, address))
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime["Date"] = formatdate(localtime=False)
    mime["Message-ID"] = message.message_id or make_msgid(domain=address.rpartition("@")[2] or None)
    if message.in_reply_to:
        mime["In-Reply-To"] = message.in_reply_to
    if message.references:
        mime["References"] = " ".join(message.references[-20:])
    if message.automatic:
        mime["Auto-Submitted"] = "auto-generated"  # keeps out-of-office replies from answering back
    if message.reply_to:
        mime["Reply-To"] = message.reply_to
    mime.set_content(message.text)
    if message.html:
        mime.add_alternative(message.html, subtype="html")
    for a in message.attachments:
        maintype, _, subtype = a.content_type.partition("/")
        mime.add_attachment(a.data, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=a.filename)
    return mime
