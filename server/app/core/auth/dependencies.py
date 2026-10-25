import hashlib
from datetime import datetime, timezone
from typing import Callable, Optional
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from starlette.requests import HTTPConnection
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.org_permissions import ORG_GRANTS
from app.core.auth.context import AuthContext, Identity
from app.core.auth.provider import BaseIAMProvider
from app.models.identity import OrgMember, User
from app.models.tenant import Tenant
from app.models.workspace_admin import ApiKey

API_KEY_PREFIX = "sk_"

_iam_provider: Optional[BaseIAMProvider] = None


def set_iam_provider(provider: Optional[BaseIAMProvider]) -> None:
    global _iam_provider
    _iam_provider = provider


def get_iam_provider() -> BaseIAMProvider:
    """The identity provider (Keycloak), built on first use. Always call this; never keep a reference."""
    global _iam_provider
    if _iam_provider is None:
        _iam_provider = build_provider()
    return _iam_provider


def build_provider() -> BaseIAMProvider:
    """Keycloak is the only way in. (Tests install their own identity source with set_iam_provider.)"""
    from app.core.auth.oidc import OidcProvider
    return OidcProvider()


def production_problems() -> list:
    """What would make a production deployment unsafe, in plain words (checked at startup; empty means fine)."""
    problems = []
    if not settings.OIDC_ISSUER.startswith("https://"):
        problems.append("OIDC_ISSUER must be an https address")
    if not settings.PUBLIC_SITE_URL.startswith("https://"):
        problems.append("PUBLIC_SITE_URL must be an https address")
    if settings.AGENT_TOKEN_SECRET == "dev-only-change-me":
        problems.append("AGENT_TOKEN_SECRET still has its development value")
    if not settings.SECRETS_ENCRYPTION_KEY:
        problems.append("SECRETS_ENCRYPTION_KEY is not set")
    for name in ("REQUIRE_TRUSTED_DEVICE", "REQUIRE_EMAIL_VERIFICATION", "REQUIRE_RECENT_LOGIN"):
        if not getattr(settings, name):
            problems.append(f"{name} must stay on")
    if settings.INVITE_REQUIRE_VERIFIED_EMAIL is False:
        problems.append("INVITE_REQUIRE_VERIFIED_EMAIL must stay on")
    return problems


def validate_auth_config() -> None:
    """Fails startup when the configuration would weaken authentication."""
    if settings.is_production and "*" in settings.CORS_ORIGINS:
        raise RuntimeError("CORS_ORIGINS may not contain '*' in production.")
    if settings.is_production:
        problems = production_problems()
        if problems:
            raise RuntimeError("Not safe to run in production: " + "; ".join(problems))
    get_iam_provider()
    from app.core.agent_token import check_agent_keys
    from app.services.notifications import check_mail_config
    check_agent_keys()
    check_mail_config()


async def _signed_in(request: Request) -> Identity:
    """The Keycloak identity, and then the second check: is this browser one the account has confirmed?"""
    from app.services import device_service
    identity = await get_iam_provider().authenticate(request)
    await device_service.gate(request, identity)
    return identity


async def get_identity(request: Request) -> Identity:
    """Who is calling, with no organization involved (onboarding, invitations, 'which orgs am I in')."""
    return await _signed_in(request)


async def _api_key_auth(key: str) -> AuthContext:
    """A workspace API key acts only inside its workspace, as a viewer ("read") or an editor ("write")."""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(ApiKey).where(ApiKey.key_hash == hashlib.sha256(key.encode()).hexdigest()))).scalar_one_or_none()
        now = datetime.now(timezone.utc)
        if row is None or row.revoked_at or (row.expires_at and row.expires_at < now):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="This API key is not valid.")
        if row.scope == "intake":
            # Intake keys work on /intake/* only (see intake_keys.intake_caller): a leaked one cannot read or change anything else.
            raise _forbidden("intake_key_only", "This key can only send documents to its channel (the /intake endpoints).")
        row.last_used_at = now
        await db.commit()
        return AuthContext(
            user_id=f"apikey:{row.id}", tenant_id=row.tenant_id, username=f"API key {row.name}",
            org_roles=[f"API_KEY:{row.scope.upper()}"], accessible_template_ids=[row.template_id],
        )


def _forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": code, "message": message})


async def _organization_gate(request: HTTPConnection, identity: Identity) -> AuthContext:
    """Gate 1: an active account with an active membership in the organization the request names."""
    slug = request.headers.get("X-Org-Slug")
    async with AsyncSessionLocal() as db:
        user = await db.get(User, identity.user_id)
        if user is None:
            raise _forbidden("onboarding_required", "Finish setting up your account to continue.")
        if user is not None and user.status != "active":
            raise _forbidden("account_disabled", "This account is disabled.")
        if not slug:
            raise HTTPException(status_code=400, detail="Name the organization (X-Org-Slug header).")
        tenant = (await db.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        member = None
        if tenant is not None and tenant.is_active:
            member = (await db.execute(select(OrgMember).where(
                OrgMember.tenant_id == tenant.id, OrgMember.user_id == identity.user_id,
            ))).scalar_one_or_none()
        if member is None:
            # One answer for "no such organization" and "not yours": never reveal which organizations exist.
            raise _forbidden("not_org_member", "You're not a member of this organization.")
        return AuthContext(
            user_id=identity.user_id, tenant_id=tenant.id, username=(user.display_name if user else identity.username) or identity.user_id,
            email=user.email if user else identity.email, email_verified=user.email_verified if user else identity.email_verified,
            org_role=member.role, org_permissions=sorted(ORG_GRANTS[member.role]), org_roles=[member.role.upper()],
            is_platform_admin=identity.user_id in settings.platform_admin_ids, auth_time=identity.auth_time,
        )


async def auth_for(user_id: str, tenant_id: str) -> Optional[AuthContext]:
    """The caller as the organization gate sees them now (for long-lived connections), or None if they no longer pass."""
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        tenant = await db.get(Tenant, tenant_id)
        member = (await db.execute(select(OrgMember).where(OrgMember.tenant_id == tenant_id, OrgMember.user_id == user_id))).scalar_one_or_none()
        if tenant is None or not tenant.is_active or member is None or (user is not None and user.status != "active"):
            return None
        return AuthContext(
            user_id=user_id, tenant_id=tenant_id, username=user.display_name if user else user_id, email=user.email if user else None,
            org_role=member.role, org_permissions=sorted(ORG_GRANTS[member.role]), org_roles=[member.role.upper()],
            is_platform_admin=user_id in settings.platform_admin_ids,
        )


async def get_current_auth(request: Request) -> AuthContext:
    """
    The caller after the organization gate: a workspace API key, or a signed-in person who is an
    active member of the organization named by X-Org-Slug.
    """
    bearer = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if bearer.startswith(API_KEY_PREFIX):
        return await _api_key_auth(bearer)
    return await _organization_gate(request, await _signed_in(request))


async def require_recent_login(auth: AuthContext = Depends(get_current_auth)) -> None:
    """
    For actions that would hurt if someone else were at the keyboard (API keys, webhooks, closing things, changing owners):
    the person must have typed their credentials in the last RECENT_LOGIN_MINUTES, or sign in again first. API keys and
    other non-interactive callers are not people at a keyboard, so this is not asked of them (and they can't get here).
    """
    if not settings.REQUIRE_RECENT_LOGIN or auth.user_id.startswith("apikey:"):
        return
    fresh = auth.auth_time is not None and datetime.now(timezone.utc).timestamp() - auth.auth_time <= settings.RECENT_LOGIN_MINUTES * 60
    if not fresh:
        raise _forbidden("reauth_required", "Sign in again to do this. It protects your account if someone else is using your computer.")


def require_org_permission(permission: str) -> Callable:
    """Dependency: the caller's organization role must grant `permission`."""
    async def dependency(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
        if not auth.can_org(permission):
            raise _forbidden("org_permission_denied", "Your organization role doesn't allow this.")
        return auth
    return dependency


async def require_org_admin(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
    if not auth.is_org_admin():
        raise _forbidden("org_permission_denied", "Administrative privileges required.")
    return auth


async def require_platform_admin(identity: Identity = Depends(get_identity)) -> Identity:
    """People who run the platform itself, named in PLATFORM_ADMIN_USER_IDS."""
    if identity.user_id not in settings.platform_admin_ids:
        raise _forbidden("platform_admin_required", "Only platform administrators can do this.")
    return identity


async def require_platform_admin_in_org(auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
    """A platform administrator acting inside an organization (agent traces hold verbatim prompts: operators only)."""
    if not auth.is_platform_admin:
        raise _forbidden("platform_admin_required", "Only platform administrators can do this.")
    return auth


async def development_only() -> None:
    """Destructive and sample routes exist only in development (ENV=development), and then only for platform admins."""
    if settings.is_production:
        raise HTTPException(status_code=404, detail="Not found.")


def require_template_access(template_id_param_name: str = "template_id") -> Callable:
    """Enforces that an API key's binding (if any) allows the template named in the path or query."""
    async def dependency(request: Request, auth: AuthContext = Depends(get_current_auth)) -> AuthContext:
        template_id = request.path_params.get(template_id_param_name) or request.query_params.get(template_id_param_name)
        if template_id and not auth.has_template_access(template_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied for this workspace.")
        return auth
    return dependency
