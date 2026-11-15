"""
The search box in the customer app: one query across the organization, showing only what the person may open.

- Workspaces they belong to (an organization admin also finds the others, as the workspace list shows them).
- Documents, only in workspaces where their role there can view documents.
- Objects of those workspaces' templates, and the things they have met (each has a profile).
- People in the organization.
- Their own chats with the agent (titles only), in workspaces they belong to.
- Support requests they can see (their own; every one of the organization's for an owner or admin).
- Invoices, only for people who manage billing.

Each item's `href` is relative to the organization (the app puts /{org} in front).
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, get_current_auth
from app.core.database import get_db_session
from app.models.billing import Invoice
from app.models.conversation import Conversation
from app.models.document import Document, DocumentRole
from app.models.identity import OrgMember, User, WorkspaceRole
from app.models.support import SupportTicket
from app.models.workspace import Workspace
from app.models.workspace_admin import WorkspaceMember
from app.services import object_profiles, search as find
from app.services.support import tickets as support_tickets

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(q: str = Query(..., min_length=2, max_length=120), db: AsyncSession = Depends(get_db_session),
                 auth: AuthContext = Depends(get_current_auth)) -> Dict[str, Any]:
    if not auth.org_role:
        raise HTTPException(status_code=403, detail={"code": "people_only", "message": "Search is for people signed in to the organization."})
    s = find.Query(q)
    tenant = auth.tenant_id

    # Where they stand in each workspace: {workspace id: the permissions of their role there}
    roles = {tid: set(perms or []) for tid, perms in (await db.execute(
        select(WorkspaceMember.template_id, WorkspaceRole.permissions).join(WorkspaceRole, WorkspaceRole.id == WorkspaceMember.role)
        .where(WorkspaceMember.tenant_id == tenant, WorkspaceMember.user_id == auth.user_id))).all() if auth.has_template_access(tid)}
    all_workspaces = {w.id: w for w in (await db.execute(select(Workspace).where(Workspace.tenant_id == tenant))).scalars().all()}
    open_to_them = [w for w in all_workspaces.values() if auth.has_template_access(w.id) and (w.id in roles or auth.is_org_admin())]
    can_view_documents = [tid for tid, perms in roles.items() if "documents.view" in perms and tid in all_workspaces]

    workspaces = sorted((w for w in open_to_them if s.lower in w.name.lower() or s.lower in w.slug.lower()),
                        key=lambda w: (0 if s.lower in (w.name.lower(), w.slug.lower()) else 1 if w.name.lower().startswith(s.lower) else 2, w.name.lower()))[:5]

    documents = []
    if can_view_documents:
        documents = (await db.execute(select(Document).where(
            Document.tenant_id == tenant, Document.template_id.in_(can_view_documents), Document.role == DocumentRole.DOCUMENT,
            or_(s.matches(Document.original_filename, Document.external_id, Document.identified_key), Document.id == s.text))
            .order_by(s.rank(Document.identified_key, Document.original_filename, Document.external_id), Document.created_at.desc()).limit(6))).scalars().all()

    found = await object_profiles.search(db, s, [tid for tid in can_view_documents if tid in all_workspaces])

    full_name = User.first_name + " " + User.last_name
    people = (await db.execute(select(User, OrgMember.role).join(OrgMember, OrgMember.user_id == User.id)
                               .where(OrgMember.tenant_id == tenant, s.matches(User.email, full_name))
                               .order_by(s.rank(full_name, User.email), User.first_name).limit(5))).all()

    member_of = [tid for tid in roles if tid in all_workspaces]
    chats = []
    if member_of:
        chats = (await db.execute(select(Conversation).where(
            Conversation.tenant_id == tenant, Conversation.user_id == auth.user_id, Conversation.kind == "chat",
            Conversation.template_id.in_(member_of), s.matches(Conversation.title))
            .order_by(s.rank(Conversation.title), Conversation.updated_at.desc()).limit(5))).scalars().all()

    ticket_cols = (SupportTicket.number, SupportTicket.subject)
    tickets = (await db.execute(support_tickets.visible(select(SupportTicket), tenant_id=tenant, user_id=auth.user_id, org_role=auth.org_role)
                                .where(s.matches(*ticket_cols)).order_by(s.rank(*ticket_cols), SupportTicket.created_at.desc()).limit(5))).scalars().all()

    invoices = []
    if auth.can_org("billing.manage"):
        invoices = (await db.execute(select(Invoice).where(Invoice.tenant_id == tenant, Invoice.status != "draft", s.matches(Invoice.number))
                                     .order_by(s.rank(Invoice.number), Invoice.issued_at.desc().nulls_last()).limit(5))).scalars().all()

    ws = lambda tid: all_workspaces[tid]  # noqa: E731
    return find.answer(s, [
        find.group("workspaces", "Workspaces", [find.item(w.id, w.name, href=f"/w/{w.slug}",
                                                          detail=None if w.id in roles else "You're not a member yet") for w in workspaces]),
        find.group("documents", "Documents", [find.item(d.id, d.identified_key or d.original_filename, href=f"/w/{ws(d.template_id).slug}/documents/{d.id}",
                                                        detail=find.joined(ws(d.template_id).name, d.original_filename if d.identified_key else None),
                                                        status=d.status.value.lower()) for d in documents]),
        find.group("things", "Things", [find.item(x["key"], x["label"], href=f"/w/{ws(x['template_id']).slug}/{x['path']}",
                                                  detail=find.joined(x["type"], ws(x["template_id"]).name, f"{x['documents']} documents"),
                                                  icon=x["icon"]) for x in found["things"]]),
        find.group("objects", "Objects", [find.item(f"{x['template_id']}:{x['type']}", x["type"], href=f"/w/{ws(x['template_id']).slug}/{x['path']}",
                                                    detail=find.joined(ws(x["template_id"]).name, f"{x['fields']} attributes"), icon=x["icon"])
                                          for x in found["objects"]]),
        find.group("chats", "Your chats", [find.item(c.id, c.title, href=f"/w/{ws(c.template_id).slug}", detail=ws(c.template_id).name,
                                                     workspace_id=c.template_id) for c in chats]),
        find.group("people", "People", [find.item(u.id, u.display_name, href="/people", detail=find.joined(u.email, role)) for u, role in people]),
        find.group("tickets", "Support requests", [find.item(t.id, f"{t.number} · {t.subject}", href=f"/settings/help/tickets?ticket={t.id}",
                                                             status=t.status) for t in tickets]),
        find.group("invoices", "Invoices", [find.item(i.id, i.number or "Invoice", href=f"/settings/billing/invoices?invoice={i.id}", status=i.status)
                                            for i in invoices]),
    ])
