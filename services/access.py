"""Who is allowed to read which company's hiring data.

Holding the ``recruiter`` role is not authorization on its own — it says *what*
someone may do, not *whose* data they may do it to. A recruiter belongs to one
company (``users.company_id``, required at registration) and every hiring read
has to be narrowed to it, or the portal is one guessed id away from showing a
rival's shortlist.

So the recruiter-facing pages never look a record up directly. They ask here,
and get either the record or an :class:`AccessError`. Three rules:

* **admin** — unrestricted. Operators need to be able to see what a recruiter is
  reporting as broken.
* **recruiter** — their own ``company_id`` and nothing else. A recruiter whose
  company is unset (only possible on a legacy row — both
  :func:`services.auth_service.register` and ``set_role`` refuse it now) reaches
  nothing rather than everything.
* **anyone else** — nothing. Candidates read their own records through
  ``application_service.for_user`` and ``interview_service.require``, which check
  ownership instead of company.

The error message is deliberately the same for "does not exist" and "belongs to
someone else". Distinguishing them turns a 403 into a directory: a recruiter
could walk the id space and learn how many candidates a competitor is running,
without ever seeing a name.
"""

from __future__ import annotations

from services import application_service as apps
from services import auth_service as auth
from services import catalog_service as catalog
from services import interview_service as ivs

# Same words for both failure modes, on purpose — see the module docstring.
_DENIED = "That record is not available."


class AccessError(Exception):
    """The caller may not see this record — missing or out of reach."""


def reach(user: auth.User) -> str | None:
    """The company this user is confined to. ``None`` means unrestricted.

    Raises :class:`AccessError` for a user with no hiring access at all, so the
    ambiguity between "sees everything" and "sees nothing" never survives past
    this function.
    """
    if user.role == "admin":
        return None
    if user.role == "recruiter" and user.company_id:
        return user.company_id
    raise AccessError("You do not have access to hiring data.")


def can_reach_company(user: auth.User, company_id: str) -> bool:
    """Whether ``user`` may read one company's data. Never raises."""
    try:
        limit = reach(user)
    except AccessError:
        return False
    return limit is None or limit == company_id


# --------------------------------------------------------------------------- #
# Guarded lookups — each returns the record or raises
# --------------------------------------------------------------------------- #


def visible_roles(user: auth.User, *, only_open: bool = False) -> list[catalog.Role]:
    """Every role this user may manage, narrowed to their company."""
    return catalog.list_roles(company_id=reach(user), only_open=only_open)


def role(user: auth.User, role_id: str) -> catalog.Role:
    found = catalog.get_role(role_id)
    if found is None or not can_reach_company(user, found.company_id):
        raise AccessError(_DENIED)
    return found


def application(user: auth.User, application_id: int) -> apps.Application:
    found = apps.get(application_id)
    if found is None:
        raise AccessError(_DENIED)
    role(user, found.role_id)  # raises if the role's company is out of reach
    return found


def interview(user: auth.User, interview_id: int) -> ivs.Interview:
    found = ivs.get(interview_id)
    if found is None:
        raise AccessError(_DENIED)
    role(user, found.role_id)
    return found
