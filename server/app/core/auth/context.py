from typing import List, Optional
from pydantic import BaseModel, Field


class Identity(BaseModel):
    """Who the identity provider says is calling. Nothing here grants access to anything."""
    user_id: str = Field(..., description="Stable subject of the account (the Keycloak `sub`)")
    username: Optional[str] = Field(None, description="Display name or email")
    email: Optional[str] = None
    email_verified: bool = False
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    # The photo a single sign-on provider (Google) shared, when it did
    picture: Optional[str] = None
    # When they last typed their credentials (epoch seconds, the token's auth_time); None if the token doesn't say
    auth_time: Optional[int] = None


class AuthContext(BaseModel):
    """
    Identity plus the organization the request is for, after the organization gate passed.
    `org_role` and `org_permissions` come from the caller's membership row, never from the token.
    """
    user_id: str = Field(..., description="Unique authenticated user identifier")
    tenant_id: str = Field(..., description="Organization the request acts in")
    username: Optional[str] = Field("User", description="Display username or email")
    email: Optional[str] = None
    email_verified: bool = False
    auth_time: Optional[int] = Field(None, description="When they last typed their credentials (epoch seconds)")
    org_role: Optional[str] = Field(None, description="owner | admin | member; None for API keys")
    org_permissions: List[str] = Field(default_factory=list)
    org_roles: List[str] = Field(default_factory=list, description="Role names for display and API-key scope (API_KEY:READ/WRITE)")
    accessible_template_ids: List[str] = Field(default_factory=lambda: ["*"], description="['*'] or the workspaces an API key is bound to")
    is_platform_admin: bool = False

    def is_org_admin(self) -> bool:
        return self.org_role in ("owner", "admin")

    def can_org(self, permission: str) -> bool:
        return permission in self.org_permissions

    def has_template_access(self, template_id: Optional[str]) -> bool:
        """Whether a key's binding allows this workspace. Membership is checked separately, per workspace."""
        if not template_id:
            return True
        return "*" in self.accessible_template_ids or template_id in self.accessible_template_ids
