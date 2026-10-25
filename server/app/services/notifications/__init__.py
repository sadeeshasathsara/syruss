"""
Everything that tells a person something, in one place: in-app notifications and email.

    from app.services.notifications import notify, Recipient, INVITATION
    await notify(INVITATION, Recipient(email="a@b.com", user_id=None), {"inviter": ..., ...})

kinds.py declares each notification, templates/email holds the email files, transport.py talks to the mail server.
"""
from app.services.notifications.inapp import ACCOUNT, push as push_in_app, retire as retire_in_app, to_dict as notification_dict
from app.services.notifications.kinds import DEVICE_ADDED, DEVICE_CODE, EMAIL_CODE, INVITATION, KINDS, Kind
from app.services.notifications.service import Channel, Delivery, Recipient, notify, recipient_for_email
from app.services.notifications.transport import EmailError, EmailMessage, check_mail_config, outbox

__all__ = [
    "ACCOUNT", "Channel", "DEVICE_ADDED", "DEVICE_CODE", "Delivery", "EMAIL_CODE", "EmailError", "EmailMessage", "INVITATION", "KINDS", "Kind", "Recipient",
    "check_mail_config", "notification_dict", "notify", "outbox", "push_in_app", "recipient_for_email", "retire_in_app",
]
