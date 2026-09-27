"""Business logic. UI-agnostic on purpose.

Nothing in this package imports Streamlit: every function here takes plain
arguments and returns plain data, so the same logic can be driven from the
Streamlit app, a test, a seeding script or a future FastAPI/LiveKit worker.
"""

from services.auth_service import (
    AuthError,
    ROLES,
    Role,
    User,
    change_password,
    create_session,
    ensure_admin_account,
    ensure_sandbox_candidate,
    get_user,
    get_user_by_email,
    has_role,
    list_users,
    login,
    logout,
    logout_all,
    purge_expired_sessions,
    recent_failures,
    register,
    require_role,
    reset_sandbox,
    resolve_session,
    sandbox_counts,
    set_active,
    set_role,
    update_profile,
)

__all__ = [
    "AuthError",
    "ROLES",
    "Role",
    "User",
    "change_password",
    "create_session",
    "ensure_admin_account",
    "ensure_sandbox_candidate",
    "get_user",
    "get_user_by_email",
    "has_role",
    "list_users",
    "login",
    "logout",
    "logout_all",
    "purge_expired_sessions",
    "recent_failures",
    "register",
    "require_role",
    "reset_sandbox",
    "resolve_session",
    "sandbox_counts",
    "set_active",
    "set_role",
    "update_profile",
]
