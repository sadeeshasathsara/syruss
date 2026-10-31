"""
The kinds of notification Syruss sends. A kind says what it needs (its variables), which channels it uses and how
it reads in each: an email (a template in templates/email plus a subject line) and/or an in-app notification (the
bell). Sending one is `notify(KIND, recipient, data)`; nothing else knows about templates or channels.

To add a notification: declare a Kind here, add its template files for the email, and call notify().
"""
from dataclasses import dataclass
from typing import Dict, Literal, Optional, Tuple


@dataclass(frozen=True)
class EmailSpec:
    template: str  # templates/email/<template>.html and .txt
    subject: str   # may use the variables: "{{ inviter }} invited you"


@dataclass(frozen=True)
class InAppSpec:
    type: str
    title: str
    body: str = ""
    link: Optional[str] = None  # an app path, or a full address
    severity: Literal["info", "success", "warning", "error"] = "info"
    # "account": follows the person across organizations (an invitation to one they aren't in yet).
    # "tenant": belongs to one organization and shows only there.
    scope: Literal["account", "tenant"] = "account"
    keep: Tuple[str, ...] = ()           # variables stored with the notification (for the app to act on)
    replace_by: Optional[str] = None     # one of `keep`: a newer notification with the same value replaces the older


@dataclass(frozen=True)
class Kind:
    key: str
    variables: Tuple[str, ...]
    optional: Tuple[str, ...] = ()
    email: Optional[EmailSpec] = None
    in_app: Optional[InAppSpec] = None

    def prepare(self, data: Dict[str, object]) -> Dict[str, object]:
        """The variables, checked: all required ones present, none unknown (a typo fails loudly, not silently blank)."""
        known = set(self.variables) | set(self.optional)
        missing = [v for v in self.variables if data.get(v) in (None, "")]
        unknown = [k for k in data if k not in known]
        if missing or unknown:
            raise ValueError(f"Notification '{self.key}': " + "; ".join(
                part for part in (f"missing {', '.join(missing)}" if missing else "", f"unknown {', '.join(unknown)}" if unknown else "") if part))
        return {**{name: None for name in self.optional}, **data}


INVITATION = Kind(
    key="invitation",
    variables=("inviter", "organization", "role", "url", "expires_on"),
    optional=("workspace", "role_description", "note", "invitation_id"),
    email=EmailSpec("invitation", "{{ inviter }} invited you to {{ workspace or organization }} on Syruss"),
    in_app=InAppSpec(
        type="invitation.received",
        title="{{ inviter }} invited you to {{ workspace or organization }}",
        body="You would join as {{ role }}. Open the invitation to accept it; it is valid until {{ expires_on }}.",
        link="{{ url }}",
        keep=("invitation_id",),
        replace_by="invitation_id",
    ),
)

EMAIL_CODE = Kind(
    key="email_code",
    variables=("code", "minutes"),
    email=EmailSpec("email_code", "{{ code }} is your Syruss confirmation code"),
)

DEVICE_CODE = Kind(
    key="device_code",
    variables=("code", "minutes", "device", "ip", "when"),
    email=EmailSpec("device_code", "{{ code }} is your Syruss sign-in code"),
)

DEVICE_ADDED = Kind(
    key="device_added",
    variables=("device", "when"),
    in_app=InAppSpec(
        type="security.device_added",
        title="New browser added to your account",
        body="{{ device }} was confirmed on {{ when }}. If that wasn't you, remove it under Account and security.",
        severity="warning",
    ),
)

KINDS: Dict[str, Kind] = {k.key: k for k in (INVITATION, EMAIL_CODE, DEVICE_CODE, DEVICE_ADDED)}
