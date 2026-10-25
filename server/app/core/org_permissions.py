"""
Organization roles. An organization is the top tenancy level: it owns people and workspaces.
Org roles decide who runs the organization; they never grant access to a workspace's data
(that takes a workspace role, see app/core/permissions.py).
"""
ORG_ROLES = {"owner": "Owner", "admin": "Admin", "member": "Member"}
ORG_ROLE_DESCRIPTIONS = {
    "owner": "Runs the organization: people, workspaces and billing.",
    "admin": "Manages people and workspaces. Opens a workspace's data only when added to it.",
    "member": "Belongs to the organization and enters the workspaces they are added to.",
}
ORG_PERMISSIONS = {
    "org.manage": "Change the organization's name and settings",
    "org.members.manage": "Invite people to the organization and change their roles",
    "workspaces.create": "Create workspaces",
    "workspaces.delete": "Delete workspaces",
    "billing.manage": "Manage the plan and billing",
    "org.audit.view": "See the organization-wide audit log and runs",
}
ORG_GRANTS = {
    "owner": set(ORG_PERMISSIONS),
    "admin": set(ORG_PERMISSIONS) - {"billing.manage"},
    "member": set(),
}
