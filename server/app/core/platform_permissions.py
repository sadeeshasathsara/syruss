"""
Platform staff roles: the people who run Syruss itself, working across every organization from the system admin app.
They are separate from organization roles (app/core/org_permissions.py) and workspace roles (app/core/permissions.py):
a staff role never makes someone a member of an organization.
"""
PLATFORM_PERMISSIONS = {
    "platform.view": "See the platform's health and figures, its organizations, people and workspaces (counts and settings, never content)",
    "tenants.manage": "Suspend, reactivate and rename organizations",
    "users.manage": "Disable and re-enable accounts, end their sessions and forget their browsers",
    "workspaces.manage": "Pause and resume workspaces and change their limits",
    "operations.manage": "Requeue stuck documents, replay deliveries, run scheduled jobs and switch the agent off",
    "billing.manage": "Change plans, limits, credits and trials",
    "support.access": "Open a customer's documents and agent traces, for a stated reason and a limited time",
    "staff.manage": "Add platform staff, change their roles and remove them",
    "system.manage": "Platform settings, maintenance mode and the development tools",
}

PLATFORM_ROLES = {
    "owner": "Everything, including who is on the platform staff.",
    "operator": "Keeps the platform running: organizations, accounts, workspaces, queues and settings.",
    "support": "Helps customers: sees the platform and, for a stated reason, a customer's documents.",
    "billing": "Plans, limits, credits and trials.",
    "viewer": "Sees the platform's health and figures; changes nothing.",
}

PLATFORM_GRANTS = {
    "owner": set(PLATFORM_PERMISSIONS),
    "operator": {"platform.view", "tenants.manage", "users.manage", "workspaces.manage", "operations.manage", "system.manage"},
    "support": {"platform.view", "support.access"},
    "billing": {"platform.view", "billing.manage"},
    "viewer": {"platform.view"},
}
