"""
Accounts and organizations: creating a person's Syruss account from their verified identity,
and describing everything they belong to (the one answer every frontend gates on).
"""
import re
import secrets as _secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth.context import Identity
from app.core.org_permissions import ORG_GRANTS, ORG_ROLES
from app.models.identity import OrgMember, User, WorkspaceRole
from app.models.tenant import Tenant
from app.models.workspace import Workspace
from app.models.workspace_admin import WorkspaceMember
from app.services import email_verification

PHONE = re.compile(r"^\+?[0-9 ()\-.]{6,24}$")


def slugify(value: str) -> str:
    value = value.lower().replace("'", "").replace("’", "")  # "Maya's" reads as mayas, not maya-s
    return "-".join("".join(c if c.isalnum() else " " for c in value).split())[:48] or "organization"


async def unique_org_slug(db: AsyncSession, base: str) -> str:
    base = slugify(base)
    taken = set((await db.execute(select(Tenant.slug).where(Tenant.slug.like(f"{base}%")))).scalars().all())
    if base not in taken:
        return base
    for _ in range(20):
        candidate = f"{base}-{_secrets.token_hex(2)}"
        if candidate not in taken:
            return candidate
    return f"{base}-{_secrets.token_hex(4)}"


def valid_timezone(name: Optional[str]) -> str:
    if not name:
        return "UTC"
    try:
        ZoneInfo(name)
        return name
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail=f"'{name}' is not a time zone.")


def clean_phone(phone: Optional[str]) -> Optional[str]:
    phone = (phone or "").strip()
    if not phone:
        return None
    if not PHONE.match(phone):
        raise HTTPException(status_code=422, detail="Enter a phone number with digits, spaces, + ( ) - only.")
    return phone


def clean_photo(url: Optional[str]) -> Optional[str]:
    """A photo from a single sign-on provider: only an https address of sensible length is kept."""
    url = (url or "").strip()
    return url if url.startswith("https://") and len(url) <= 512 else None


def user_dict(u: User) -> Dict[str, Any]:
    return {"id": u.id, "email": u.email, "email_verified": u.email_verified, "first_name": u.first_name, "last_name": u.last_name,
            "name": u.display_name, "phone": u.phone, "job_title": u.job_title, "timezone": u.timezone, "avatar_url": u.avatar_url}


async def sync_user(db: AsyncSession, user: User, identity: Identity) -> None:
    """The identity provider owns the email: follow it, never the other way round."""
    changed = False
    if identity.email and identity.email != user.email:
        taken = (await db.execute(select(User.id).where(User.email == identity.email, User.id != user.id))).first()
        if not taken:
            user.email, changed = identity.email, True
    if identity.email_verified and not user.email_verified and identity.email == user.email:  # confirmed here or by the provider: never undone
        user.email_verified, changed = True, True
    photo = clean_photo(identity.picture)
    if photo and photo != user.avatar_url:
        user.avatar_url, changed = photo, True
    user.last_seen_at = datetime.now(timezone.utc)
    if changed:
        await db.flush()


async def context_for(db: AsyncSession, identity: Identity) -> Dict[str, Any]:
    """Who this is, which organizations and workspaces they belong to, and what their roles allow."""
    user = await db.get(User, identity.user_id)
    base: Dict[str, Any] = {
        "identity": {"email": identity.email, "email_verified": await email_verification.is_verified(db, identity),
                     "given_name": identity.given_name, "family_name": identity.family_name, "picture": clean_photo(identity.picture)},
        "onboarded": user is not None, "user": None, "organizations": [], "workspaces": [],
    }
    if user is None:
        return base
    await sync_user(db, user, identity)
    orgs = (await db.execute(
        select(OrgMember, Tenant).join(Tenant, Tenant.id == OrgMember.tenant_id)
        .where(OrgMember.user_id == user.id, Tenant.is_active.is_(True)).order_by(OrgMember.created_at)
    )).all()
    # The organization they were last in comes first (it is where signing in opens); the rest stay in the order joined.
    orgs.sort(key=lambda row: row[1].id != user.last_org_id)
    workspaces = (await db.execute(
        select(WorkspaceMember, Workspace, Tenant, WorkspaceRole)
        .join(Workspace, Workspace.id == WorkspaceMember.template_id)
        .join(Tenant, Tenant.id == WorkspaceMember.tenant_id)
        .join(WorkspaceRole, WorkspaceRole.id == WorkspaceMember.role)
        .where(WorkspaceMember.user_id == user.id, Tenant.is_active.is_(True)).order_by(WorkspaceMember.created_at)
    )).all()
    base["user"] = user_dict(user)
    base["organizations"] = [
        {"id": t.id, "slug": t.slug, "name": t.name, "role": m.role, "role_label": ORG_ROLES[m.role],
         "permissions": sorted(ORG_GRANTS[m.role]), "last_used": t.id == user.last_org_id} for m, t in orgs
    ]
    base["workspaces"] = [
        {"id": w.id, "slug": w.slug, "name": w.name, "org_slug": t.slug, "role": r.id, "role_label": r.name,
         "permissions": sorted(r.permissions or [])} for _, w, t, r in workspaces
    ]
    return base


RESERVED_SLUGS = {"api", "new", "w", "admin", "www", "site", "signed-out", "signin", "signup", "continue", "onboarding", "invite", "login", "logout", "static", "_next"}
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,46})[a-z0-9]$")


def clean_slug(value: str, derive: bool = False) -> str:
    """An address as typed must already be valid; one made from a name is tidied first."""
    slug = slugify(value) if derive else value.strip()
    if not SLUG.match(slug) or slug in RESERVED_SLUGS:
        raise HTTPException(status_code=422, detail="Use 3 to 48 letters, numbers and hyphens for the address, and not a reserved word.")
    return slug


async def slug_is_free(db: AsyncSession, slug: str) -> bool:
    return (await db.execute(select(Tenant.id).where(Tenant.slug == slug))).first() is None


async def create_organization(db: AsyncSession, identity: Identity, *, name: str, slug: Optional[str]) -> Dict[str, Any]:
    """A new organization owned by an existing account. The address (slug) is theirs to choose and must be free."""
    user = await db.get(User, identity.user_id)
    if user is None:
        raise HTTPException(status_code=403, detail={"code": "onboarding_required", "message": "Finish setting up your account first."})
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Give the organization a name.")
    chosen = clean_slug(slug) if slug else clean_slug(name, derive=True)
    if not await slug_is_free(db, chosen):
        if slug:
            raise HTTPException(status_code=409, detail=f"The address “{chosen}” is taken. Try another.")
        chosen = await unique_org_slug(db, chosen)
    tenant = Tenant(name=name, slug=chosen, is_active=True)
    db.add(tenant)
    await db.flush()
    db.add(OrgMember(tenant_id=tenant.id, user_id=user.id, role="owner"))
    await db.commit()
    return await context_for(db, identity)


async def onboard(db: AsyncSession, identity: Identity, *, first_name: str, last_name: str, phone: Optional[str],
                  timezone_name: Optional[str]) -> Dict[str, Any]:
    """
    Creates the person's account from their verified identity: who they are, nothing about organizations
    (they create or join one next). Safe to repeat: an existing account is returned as is.
    """
    if await db.get(User, identity.user_id):
        return await context_for(db, identity)
    if not identity.email:
        raise HTTPException(status_code=422, detail="Your sign-in has no email address, so an account can't be created.")
    await email_verification.require_verified(db, identity)
    if (await db.execute(select(User.id).where(User.email == identity.email))).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    first, last = first_name.strip(), last_name.strip()
    user = User(id=identity.user_id, email=identity.email, email_verified=await email_verification.is_verified(db, identity), first_name=first, last_name=last,
                phone=clean_phone(phone), timezone=valid_timezone(timezone_name), last_seen_at=datetime.now(timezone.utc),
                avatar_url=clean_photo(identity.picture))
    db.add(user)
    await db.flush()
    await db.commit()
    return await context_for(db, identity)
