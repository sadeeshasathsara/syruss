"""
What happens to data when it is no longer wanted.

    workspace_files_removed   a deleted workspace's files leave storage with it (the database rows go by cascade)
    purge_closed              an organization its owner closed keeps everything for CLOSED_ORG_RETENTION_DAYS, in case it
                              was a mistake; then its workspaces, documents, records and files are removed for good. Its
                              invoices, payments and the audit trail stay: they are financial records.
    close_account             a person leaving Syruss: out of every organization and workspace, their profile blanked.
                              Their sign-in itself lives in Keycloak; staff are told to remove it there.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.models.audit import AuditLog
from app.models.identity import OrgInvitation, OrgMember, TrustedDevice, User
from app.models.notification import Notification
from app.models.template import DocumentTemplate
from app.models.tenant import Tenant
from app.models.workspace import Workspace
from app.models.workspace_admin import WorkspaceMember
from app.services.storage_service import storage_service

logger = logging.getLogger("core.lifecycle")


def workspace_prefix(tenant_id: str, template_id: str) -> str:
    return f"tenants/{tenant_id}/templates/{template_id}/"


async def workspace_files_removed(tenant_id: str, template_id: str) -> int:
    """Removes a deleted workspace's files from storage. Never raises: a file left behind is logged, not a failed request."""
    try:
        return await asyncio.to_thread(storage_service.remove_prefix, workspace_prefix(tenant_id, template_id))
    except Exception:
        logger.exception("Could not remove the files of workspace %s", template_id)
        return 0


async def purge_closed(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """Removes the content of organizations closed longer ago than the retention period. Returns how many were purged."""
    now = now or datetime.now(timezone.utc)
    if settings.CLOSED_ORG_RETENTION_DAYS <= 0:
        return 0
    cutoff = now - timedelta(days=settings.CLOSED_ORG_RETENTION_DAYS)
    due = (await db.execute(select(Tenant).where(Tenant.status == "closed", Tenant.purged_at.is_(None),
                                                 Tenant.status_changed_at < cutoff))).scalars().all()
    purged = 0
    for tenant in due:
        try:
            tenant_id = tenant.id
            workspaces = (await db.execute(select(func.count(Workspace.id)).where(Workspace.tenant_id == tenant_id))).scalar() or 0
            await db.execute(delete(Workspace).where(Workspace.tenant_id == tenant_id))
            await db.execute(delete(DocumentTemplate).where(DocumentTemplate.tenant_id == tenant_id))  # documents, records, chats go with it
            await db.execute(delete(OrgInvitation).where(OrgInvitation.tenant_id == tenant_id))
            tenant.purged_at = now
            db.add(AuditLog(tenant_id=tenant_id, event_type="ORG_DATA_REMOVED", entity_type="Organization", entity_id=tenant_id, actor="system",
                            details={"workspaces": workspaces, "closed_at": tenant.status_changed_at.isoformat() if tenant.status_changed_at else None,
                                     "kept": "invoices, payments and this activity log"}))
            await db.commit()
            files = await asyncio.to_thread(storage_service.remove_prefix, f"tenants/{tenant_id}/")
            logger.info("Purged closed organization %s: %s workspaces, %s files", tenant_id, workspaces, files)
            purged += 1
        except Exception:
            await db.rollback()
            logger.exception("Purging organization %s failed", tenant.id)
    return purged


async def close_account(db: AsyncSession, user: User) -> None:
    """
    Takes a person out of Syruss (caller commits). Refused while they are the only owner of an organization that is still
    open: it would be left with nobody able to run it. What they did stays in the audit trails under their id, without a name.
    """
    owned = (await db.execute(
        select(Tenant.name).join(OrgMember, OrgMember.tenant_id == Tenant.id)
        .where(OrgMember.user_id == user.id, OrgMember.role == "owner", Tenant.status != "closed"))).scalars().all()
    for name in owned:
        others = (await db.execute(
            select(func.count(OrgMember.id)).join(Tenant, Tenant.id == OrgMember.tenant_id)
            .where(Tenant.name == name, OrgMember.role == "owner", OrgMember.user_id != user.id))).scalar() or 0
        if not others:
            raise HTTPException(status_code=409, detail=(
                f"You are the only owner of {name}. Make someone else an owner, or close the organization, before deleting your account."))
    await db.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id == user.id))
    await db.execute(delete(OrgMember).where(OrgMember.user_id == user.id))
    await db.execute(delete(TrustedDevice).where(TrustedDevice.user_id == user.id))
    await db.execute(delete(Notification).where(Notification.user_id == user.id))
    user.status = "deleted"
    user.email = f"deleted-{user.id}@deleted.invalid"
    user.first_name, user.last_name, user.phone, user.job_title, user.avatar_url = "Deleted", "account", None, None, None
    user.email_verified = False
