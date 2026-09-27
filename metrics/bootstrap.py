"""Point the harness at a throwaway database and the fake provider.

:mod:`core.config` snapshots ``os.environ`` at import time behind an
``lru_cache``, so anything that wants a different database or a different LLM
provider has to say so *before the first project import*. That is the same
constraint ``tests/conftest.py`` lives under, and it is the reason this module
exists as a separate import rather than a function: importing it has the effect.

**Import this first.** :mod:`metrics.run_all` does::

    from metrics import bootstrap   # noqa: F401  — must precede core imports

Two things follow from this that matter for the paper:

* No number in the harness can touch ``data/app.db``. The developer's real
  database is never read, so nothing measured here depends on local state that
  a reviewer cannot reproduce.
* ``LLM_PROVIDER=fake`` means every timing figure excludes model inference. That
  is deliberate: it isolates the portal's own overhead, which is the part this
  project wrote. Model latency belongs in a separate table with the model name,
  quantisation and hardware beside it, and the harness reports it as unavailable
  rather than guessing.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TMP_ROOT = Path(tempfile.mkdtemp(prefix="portal-metrics-"))

_ENV = {
    "DATABASE_PATH": str(TMP_ROOT / "metrics.db"),
    "MEDIA_DIR": str(TMP_ROOT / "media"),
    "UPLOAD_DIR": str(TMP_ROOT / "uploads"),
    "LLM_PROVIDER": "fake",
    "STT_PROVIDER": "disabled",
    "TTS_PROVIDER": "disabled",
    "PROCTORING_ENABLED": "false",
    "ADMIN_EMAIL": "admin@metrics.local",
    "ADMIN_PASSWORD": "metrics-harness-1234",
    "RECRUITER_INVITE_CODE": "metrics-invite-code",
}

os.environ.update(_ENV)

# Recorded so the results file can state what the run was configured with,
# rather than the reader having to trust this docstring.
ENVIRONMENT_OVERRIDES = dict(_ENV)
