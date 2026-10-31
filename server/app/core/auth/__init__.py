from app.core.auth.context import AuthContext, Identity
from app.core.auth.provider import BaseIAMProvider
from app.core.auth.dependencies import (
    auth_for,
    get_current_auth,
    get_iam_provider,
    get_identity,
    require_recent_login,
    require_template_access,
    require_org_admin,
    require_org_permission,
    development_only,
    require_platform_admin,
    require_platform_admin_in_org,
    set_iam_provider,
    validate_auth_config,
)

__all__ = [
    "auth_for",
    "AuthContext",
    "Identity",
    "BaseIAMProvider",
    "get_current_auth",
    "get_iam_provider",
    "get_identity",
    "require_recent_login",
    "require_template_access",
    "require_org_admin",
    "require_org_permission",
    "development_only",
    "require_platform_admin",
    "require_platform_admin_in_org",
    "set_iam_provider",
    "validate_auth_config",
]
