"""
Workspace roles (RBAC). Keycloak proves who someone is; what they may do inside a workspace comes
from their membership here, resolved on every request.

Getting into a workspace takes two gates: an active organization membership (core/auth), then a
workspace membership (core/scope.py). A member's role is a row in `workspace_roles`: a system role
(owner, admin, editor, reviewer, viewer) or a custom one the workspace defined, each a set of
permissions from the closed catalogue below. API keys act as a viewer ("read") or an editor ("write").
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set
from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import AuthContext, get_current_auth
from app.core.database import get_db_session
from app.core.scope import TemplateScope, get_template_scope
from app.models.identity import WorkspaceRole

# key -> short label (also used in "your role can't ..." messages)
PERMISSIONS = {
    "documents.view": "See documents and their extracted records",
    "documents.upload": "Upload and process documents",
    "documents.review": "Review documents and answer the agent",
    "screens.manage": "Create and edit analytics screens",
    "knowledge.view": "See the workspace's knowledge and data sources",
    "knowledge.manage": "Manage knowledge and data sources",
    "template.publish": "Publish template changes",
    "agent.chat": "Chat with the agent",
    "members.view": "See who is in the workspace and their roles",
    "members.manage": "Invite people and change their roles",
    "roles.manage": "Create and edit custom roles",
    "integrations.manage": "Manage API keys and webhooks",
    "audit.view": "See the audit log",
    "workspace.manage": "Change workspace settings",
    "billing.manage": "Manage the plan and billing",
    "workspace.delete": "Delete the workspace",
}
# What each permission means in plain language, for the roles screen, grouped by area.
PERMISSION_GROUPS = [
    {"key": "documents", "label": "Documents", "permissions": [
        ("documents.view", "Open documents and see the records extracted from them."),
        ("documents.upload", "Upload documents and start processing."),
        ("documents.review", "Correct extracted records and answer the agent's questions."),
    ]},
    {"key": "screens", "label": "Analytics", "permissions": [
        ("screens.manage", "Build, change and delete the workspace's analytics screens. Anyone who can see documents can open them."),
    ]},
    {"key": "knowledge", "label": "Knowledge and template", "permissions": [
        ("knowledge.view", "See what Syruss knows about this workspace and its data sources."),
        ("knowledge.manage", "Approve, edit and remove knowledge; connect data sources."),
        ("template.publish", "Change the document template and publish new versions."),
    ]},
    {"key": "agent", "label": "Syruss, the agent", "permissions": [
        ("agent.chat", "Chat with Syruss. Conversations are private to each person."),
    ]},
    {"key": "people", "label": "People and access", "permissions": [
        ("members.view", "See who is in the workspace and their roles."),
        ("members.manage", "Invite people and change their roles."),
        ("roles.manage", "Create, edit and delete custom roles."),
        ("integrations.manage", "Create API keys and webhooks."),
    ]},
    {"key": "workspace", "label": "Workspace", "permissions": [
        ("audit.view", "See the audit log of everything done in the workspace."),
        ("workspace.manage", "Change workspace name, preferences and agent settings."),
        ("billing.manage", "Manage the plan and billing."),
        ("workspace.delete", "Delete the workspace for everyone."),
    ]},
]

# Doing something takes seeing it: a role granting the left side always includes what's on the right.
IMPLIES = {
    "documents.upload": ["documents.view"], "documents.review": ["documents.view"], "template.publish": ["documents.view"],
    "screens.manage": ["documents.view"], "knowledge.manage": ["knowledge.view"], "members.manage": ["members.view"], "roles.manage": ["members.view"],
}

ALL = set(PERMISSIONS)
_VIEW = {"documents.view", "knowledge.view", "agent.chat", "members.view"}
# The built-in roles. Their ids equal their keys; they are the same in every workspace and cannot be edited.
SYSTEM_ROLES: Dict[str, dict] = {
    "owner": {"name": "Owner", "description": "Everything, including billing and deleting the workspace.", "permissions": ALL},
    "admin": {"name": "Admin", "description": "Runs the workspace: settings, members, roles, integrations, the template and its knowledge.",
              "permissions": ALL - {"workspace.delete", "billing.manage"}},
    "editor": {"name": "Editor", "description": "Runs the work: uploads documents, reviews, manages knowledge and analytics screens, and publishes template changes.",
               "permissions": _VIEW | {"template.publish", "knowledge.manage", "documents.upload", "documents.review", "screens.manage"}},
    "reviewer": {"name": "Reviewer", "description": "Reviews documents and answers the agent's questions.",
                 "permissions": _VIEW | {"documents.review"}},
    "viewer": {"name": "Viewer", "description": "Sees everything in the workspace, changes nothing.", "permissions": set(_VIEW)},
}
# Kept for code that only needs the built-in grants (API keys, the agent's autonomous work).
GRANTS = {k: set(v["permissions"]) for k, v in SYSTEM_ROLES.items()}
ROLES = {k: v["name"] for k, v in SYSTEM_ROLES.items()}


async def ensure_system_roles(db: AsyncSession) -> None:
    """Creates or refreshes the built-in roles (safe to run on every start)."""
    for key, spec in SYSTEM_ROLES.items():
        row = await db.get(WorkspaceRole, key)
        perms = sorted(spec["permissions"])
        if row is None:
            db.add(WorkspaceRole(id=key, name=spec["name"], description=spec["description"], is_system=True, permissions=perms))
        else:
            row.name, row.description, row.is_system, row.permissions = spec["name"], spec["description"], True, perms
    await db.commit()


def clean_permissions(values: Iterable[str]) -> List[str]:
    unknown = sorted(set(values) - ALL)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown permissions: {', '.join(unknown)}.")
    granted = set(values)
    for permission in list(granted):
        granted.update(IMPLIES.get(permission, []))
    return sorted(granted)


def permission_sentence(permission: str) -> str:
    label = PERMISSIONS[permission]
    return f"{label[0].lower()}{label[1:]}"


@dataclass
class Access:
    scope: TemplateScope
    auth: AuthContext
    role: str  # the role id
    role_name: str = ""
    permissions: Set[str] = field(default_factory=set)

    def can(self, permission: str) -> bool:
        return permission in self.permissions


async def permissions_of(db: AsyncSession, role_id: str) -> Set[str]:
    role = await db.get(WorkspaceRole, role_id)
    return set(role.permissions or []) if role else set()


async def role_in(db: AsyncSession, template_id: str, role_id: str) -> Optional[WorkspaceRole]:
    """A role a member of this workspace may hold: a system role or one of this workspace's own."""
    role = await db.get(WorkspaceRole, role_id)
    if role and (role.is_system or role.template_id == template_id):
        return role
    return None


async def get_access(
    scope: TemplateScope = Depends(get_template_scope),
    auth: AuthContext = Depends(get_current_auth),
) -> Access:
    return Access(scope=scope, auth=auth, role=scope.role_id, role_name=scope.role_name, permissions=set(scope.permissions))


def _denied(role_name: str, permission: str) -> HTTPException:
    return HTTPException(status_code=403, detail=f"Your role ({role_name}) can't {permission_sentence(permission)}.")


def require(permission: str) -> Callable:
    """Dependency: the caller's role in this workspace must grant `permission`."""
    async def dependency(access: Access = Depends(get_access)) -> Access:
        if not access.can(permission):
            raise _denied(access.role_name, permission)
        return access
    return dependency


async def check_permission(db: AsyncSession, scope: TemplateScope, auth: AuthContext, permission: str) -> str:
    """The same check as `require`, for routes that resolve their workspace themselves."""
    if permission not in scope.permissions:
        raise _denied(scope.role_name, permission)
    return scope.role_id
