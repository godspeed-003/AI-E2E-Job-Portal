"""Streamlit-side session handling.

The auth service knows nothing about Streamlit; this module is the only bridge.
It keeps the raw session token in two places:

* ``st.session_state`` — fast path, valid for as long as the tab lives.
* a browser cookie — so a refresh (or a crash mid-interview) does not force a
  new login. Cookies come from ``extra-streamlit-components``, which renders a
  hidden component; if that component cannot load, everything still works and
  the user simply signs in again.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import streamlit as st

from core import db
from core.config import settings
from services import auth_service as auth
from services import catalog_service

log = logging.getLogger(__name__)

COOKIE_NAME = "portal_session"
_TOKEN_KEY = "_auth_token"
_USER_KEY = "_auth_user"
_BOOT_KEY = "_cookie_boot"


# --------------------------------------------------------------------------- #
# One-time process bootstrap
# --------------------------------------------------------------------------- #


@st.cache_resource(show_spinner=False)
def bootstrap() -> dict[str, Any]:
    """Create the schema, the bootstrap admin, and sweep dead sessions.

    Wrapped in ``cache_resource`` so it runs once per process rather than once
    per rerun.
    """
    settings.ensure_dirs()
    db.init_db()
    admin = auth.ensure_admin_account()
    seeded = catalog_service.seed_from_json()
    purged = auth.purge_expired_sessions()
    log.info(
        "Bootstrap complete (admin=%s, seeded=%s, purged_sessions=%s)",
        bool(admin),
        seeded,
        purged,
    )
    return {
        "admin_email": admin.email if admin else None,
        "seeded": seeded,
        "purged": purged,
    }


# --------------------------------------------------------------------------- #
# Cookies
#
# Two things about ``extra-streamlit-components`` shape this whole section:
#
# * The manager renders a hidden component, and a component only reports its
#   real value on the rerun it triggers itself. Caching the manager object
#   across runs therefore freezes ``.cookies`` at the empty default it returned
#   on its first render — the cookie would never be read at all. So it is built
#   fresh once per script run, in :func:`start_run`.
# * A write only reaches the browser if the script run that issued it finishes
#   normally. Sign-in and sign-out both end in ``st.rerun()``, which throws that
#   run's deltas away, so writes are queued and flushed on the following run.
# --------------------------------------------------------------------------- #

_MGR_KEY = "_cookie_mgr"
_PENDING_KEY = "_cookie_pending"
_OPS_KEY = "_cookie_ops"


def _new_manager() -> Any | None:
    try:
        import extra_streamlit_components as stx

        return stx.CookieManager(key="portal_cookies")
    except Exception as exc:  # pragma: no cover - depends on the browser
        log.warning(
            "Cookie manager unavailable, sessions will not survive refresh: %s", exc
        )
        return None


def start_run() -> None:
    """Mount the cookie component and flush a queued write. Once, first, per run."""
    st.session_state[_MGR_KEY] = _new_manager()
    st.session_state[_OPS_KEY] = []
    pending = st.session_state.pop(_PENDING_KEY, None)
    if not pending:
        return
    operation, token = pending
    if operation == "set":
        _cookie_set(token)
    else:
        _cookie_clear()


def _cookie_manager() -> Any | None:
    return st.session_state.get(_MGR_KEY)


def _op_once(name: str) -> bool:
    """Guard against issuing the same cookie op twice in one run.

    The component is keyed, so a second identical call in the same run is a
    duplicate-key error rather than a second cookie write.
    """
    done = st.session_state.setdefault(_OPS_KEY, [])
    if name in done:
        return False
    done.append(name)
    return True


def _cookie_get() -> str:
    manager = _cookie_manager()
    if manager is None:
        return ""
    try:
        return str(manager.get(COOKIE_NAME) or "")
    except Exception:
        return ""


def _cookie_set(token: str) -> None:
    manager = _cookie_manager()
    if manager is None or not _op_once("set"):
        return
    try:
        manager.set(
            COOKIE_NAME,
            token,
            key="portal_cookie_set",
            expires_at=db.utc_now().replace(tzinfo=None)
            + timedelta(hours=max(1, settings.auth.session_ttl_hours)),
            same_site="lax",
        )
    except Exception as exc:
        log.warning("Could not persist the session cookie: %s", exc)


def _cookie_clear() -> None:
    manager = _cookie_manager()
    if manager is None or not _op_once("delete"):
        return
    try:
        manager.delete(COOKIE_NAME, key="portal_cookie_del")
    except Exception:
        # delete() raises if the cookie was never in its local mirror; harmless.
        pass


# --------------------------------------------------------------------------- #
# Current user
# --------------------------------------------------------------------------- #


def current_user() -> auth.User | None:
    """The signed-in user, or ``None``.

    Cheap on repeat calls within a rerun: the resolved user is memoised in
    session state and only re-read from the database when the token changes.
    """
    token = st.session_state.get(_TOKEN_KEY) or ""
    if not token:
        token = _cookie_get()
        if token:
            st.session_state[_TOKEN_KEY] = token

    if not token:
        _forget()
        return None

    cached = st.session_state.get(_USER_KEY)
    if isinstance(cached, auth.User):
        return cached

    user = auth.resolve_session(token)
    if user is None:
        _forget()
        _cookie_clear()
        return None
    st.session_state[_USER_KEY] = user
    return user


def refresh_user() -> auth.User | None:
    """Drop the memoised user so the next read hits the database."""
    st.session_state.pop(_USER_KEY, None)
    return current_user()


def _forget() -> None:
    st.session_state.pop(_TOKEN_KEY, None)
    st.session_state.pop(_USER_KEY, None)


def sign_in(user: auth.User, token: str) -> None:
    st.session_state[_TOKEN_KEY] = token
    st.session_state[_USER_KEY] = user
    # Queued, not written: the caller reruns immediately, and a component render
    # from a discarded run never reaches the browser. See the Cookies section.
    st.session_state[_PENDING_KEY] = ("set", token)


def sign_out() -> None:
    token = st.session_state.get(_TOKEN_KEY) or ""
    if token:
        auth.logout(token)
    _forget()
    st.session_state[_PENDING_KEY] = ("delete", "")
    # Page-local scratch state must not leak into the next account's session.
    for key in [k for k in st.session_state.keys() if str(k).startswith("view_")]:
        st.session_state.pop(key, None)


# --------------------------------------------------------------------------- #
# Boot handshake
# --------------------------------------------------------------------------- #


def restore_session() -> auth.User | None:
    """Resolve the user, giving the cookie component one rerun to report first.

    Without this, every refresh would flash the login screen: the cookie
    component returns ``{}`` on its first render and only sends the real cookies
    on the rerun it triggers itself.
    """
    user = current_user()
    if user is not None:
        st.session_state[_BOOT_KEY] = True
        return user

    if st.session_state.get(_BOOT_KEY) or _cookie_manager() is None:
        return None

    st.session_state[_BOOT_KEY] = True
    _splash()
    st.stop()
    return None  # unreachable, keeps type checkers happy


def _splash() -> None:
    from ui import theme

    theme.inject()
    theme.hide_sidebar()
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        st.markdown(
            "<div style='text-align:center;padding:5rem 0 1rem'>"
            "<div style='font-size:2rem'>◐</div>"
            "<div style='margin-top:.6rem;font-weight:600'>Restoring your session…</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        # Escape hatch: if the cookie component is blocked it will never report,
        # and this button gets the user to the login form anyway.
        st.button("Sign in instead", use_container_width=True)


# --------------------------------------------------------------------------- #
# Page-level gating
# --------------------------------------------------------------------------- #


def require(*roles: str) -> auth.User:
    """Stop rendering the page unless the visitor holds one of ``roles``.

    Defence in depth: navigation already hides pages a role cannot use, but a
    bookmarked URL must not be enough to reach one.
    """
    user = current_user()
    if user is None:
        st.warning("Please sign in to continue.")
        st.stop()
    if not auth.has_role(user, *roles):
        st.error("You do not have access to this page.")
        st.caption(f"Signed in as {user.email} ({user.role}).")
        st.stop()
    return user


def sandbox_view() -> bool:
    """Whether the current viewer is looking at sandbox data."""
    user = current_user()
    if user is None:
        return False
    if user.is_sandbox:
        return True
    return bool(st.session_state.get("sandbox_mode"))


def set_sandbox_view(enabled: bool) -> None:
    st.session_state["sandbox_mode"] = bool(enabled)
