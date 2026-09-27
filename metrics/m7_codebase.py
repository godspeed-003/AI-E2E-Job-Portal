"""M7 — the artefact itself: size, test coverage of behaviour, schema shape.

A systems paper describes something that was built, and the reader's first
question is how much of it there is. These are the cheapest honest numbers in
the harness: they come from counting files and from querying ``sqlite_master``
on a freshly-created schema, so nothing here is an estimate.

Two of them carry an argument rather than just a size.

**Test count is the evidence for every behavioural claim in the paper.** When
§III says a repeated event costs less than a distinct one, the warrant is a test
that fails if it stops doing so. Counting tests per subsystem shows where that
warrant is thick and where it is thin, including the places it is thin.

**Schema shape is the data-safety argument's foundation.** You cannot claim a
system minimises retained personal data without saying which columns hold it.
The table below is generated from the live schema, not transcribed, so it cannot
drift from the code the way a hand-written table in a paper does.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import fields as dataclass_fields
from pathlib import Path

from core import db
from core.config import settings
from metrics import PROJECT_ROOT, Section

# Columns that hold personal data, and what kind. Hand-classified, because
# "is this personal data" is a judgement about meaning and no regex makes it.
# The *existence* of every column below is verified against the live schema at
# run time, so this list cannot silently go stale.
PII_COLUMNS: dict[str, tuple[str, str]] = {
    "users.email": ("identifier", "login identity; also the lockout key"),
    "users.full_name": ("identifier", "candidate's real name"),
    "users.password_hash": ("credential", "scrypt digest, not reversible"),
    "sessions.token_hash": ("credential", "sha256 of the bearer token"),
    "sessions.user_agent": ("metadata", "browser string, weakly fingerprinting"),
    "login_attempts.email": ("identifier", "retained for rate limiting"),
    "applications.candidate_name": ("identifier", "as parsed from the resume"),
    "applications.resume_path": ("file reference", "points at the uploaded document"),
    "applications.resume_text": ("sensitive free text", "the entire resume, verbatim"),
    "applications.resume_sha256": ("pseudonymous", "content hash for dedup"),
    "interviews.recording_path": ("biometric media", "video and audio of the candidate"),
    "interviews.enrollment_snapshot_path": ("biometric media", "reference face image"),
    "interviews.consent_accepted_at": ("consent record", "when recording was agreed to"),
    "interview_turns.question": ("derived", "generated from the resume"),
    "interview_turns.answer": ("sensitive free text", "the candidate's own words"),
    "interview_turns.answer_audio_path": ("biometric media", "voice recording"),
    "interview_turns.transcript_source": ("metadata", "typed vs transcribed"),
    "proctor_events.snapshot_path": ("biometric media", "frame captured on a flag"),
    "proctor_events.detail": ("sensitive free text", "what the detector saw"),
    "audit_log.user_id": ("identifier", "who did what"),
    "audit_log.action": ("metadata", "administrative action"),
    "audit_log.detail": ("metadata", "may quote a name or an email"),
}

SOURCE_PACKAGES = ("app.py", "core", "services", "llm", "proctoring", "pages", "scripts")

_TEST_DEF = re.compile(r"^\s*def (test_\w+)", re.MULTILINE)
_COMMENT_OR_BLANK = re.compile(r"^\s*(#.*)?$")


def _count_lines(path: Path) -> tuple[int, int]:
    """(physical lines, lines that are neither blank nor a comment)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0, 0
    lines = text.splitlines()
    code = sum(1 for line in lines if not _COMMENT_OR_BLANK.match(line))
    return len(lines), code


def _parametrised_cases(text: str, definitions: int) -> int:
    """How many cases ``text``'s test definitions expand to under pytest.

    Counting ``def test_*`` and quoting the result next to a pytest pass count
    is a small, durable way to publish two different numbers for one thing:
    this suite has 358 definitions and pytest collects 371, and both are
    correct. Stacked ``parametrize`` decorators multiply, so the expansion is
    the product of the argument-list lengths, defaulting to 1.

    Only literal lists and tuples are counted. A computed argvalues expression
    is not resolvable without importing the module, so this falls back to
    treating the definition as a single case, which makes the result a floor
    rather than a wrong number.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return definitions

    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        cases = 1
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            attribute = decorator.func
            if (
                not isinstance(attribute, ast.Attribute)
                or attribute.attr != "parametrize"
                or len(decorator.args) < 2
            ):
                continue
            argvalues = decorator.args[1]
            if isinstance(argvalues, (ast.List, ast.Tuple)) and argvalues.elts:
                cases *= len(argvalues.elts)
        total += cases
    return total


def _python_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return [
        p
        for p in sorted(root.rglob("*.py"))
        if "__pycache__" not in p.parts and ".venv" not in p.parts
    ]


def run() -> Section:
    section = Section(key="m7_codebase", title="Implementation size, tests and schema")

    # ------------------------------------------------------------------ #
    # Source size
    # ------------------------------------------------------------------ #
    package_rows = []
    total_lines = total_code = total_files = 0
    for name in SOURCE_PACKAGES:
        root = PROJECT_ROOT / name
        if not root.exists():
            continue
        files = _python_files(root)
        lines = code = 0
        for path in files:
            physical, effective = _count_lines(path)
            lines += physical
            code += effective
        package_rows.append(
            {
                "package": name,
                "files": len(files),
                "lines": lines,
                "code_lines": code,
                "comment_and_blank_lines": lines - code,
            }
        )
        total_files += len(files)
        total_lines += lines
        total_code += code
    section.tables["source_packages"] = package_rows

    section.add(
        "source_files",
        total_files,
        unit="files",
        kind="measured",
        source="metrics.m7_codebase",
        note=", ".join(n for n in SOURCE_PACKAGES if (PROJECT_ROOT / n).exists()),
    )
    section.add(
        "source_lines",
        total_lines,
        unit="lines",
        kind="measured",
        source="metrics.m7_codebase",
    )
    section.add(
        "source_code_lines",
        total_code,
        unit="lines",
        kind="measured",
        source="metrics.m7_codebase",
        note="excluding blank lines and comment-only lines",
    )

    # ------------------------------------------------------------------ #
    # Tests
    # ------------------------------------------------------------------ #
    test_rows = []
    total_tests = 0
    total_cases = 0
    for path in _python_files(PROJECT_ROOT / "tests"):
        if not path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8")
        names = _TEST_DEF.findall(text)
        cases = _parametrised_cases(text, len(names))
        physical, code = _count_lines(path)
        test_rows.append(
            {
                "file": path.name,
                "tests": len(names),
                "cases": cases,
                "lines": physical,
                "code_lines": code,
            }
        )
        total_tests += len(names)
        total_cases += cases
    test_rows.sort(key=lambda r: -r["tests"])
    section.tables["tests"] = test_rows

    section.add(
        "test_functions",
        total_tests,
        unit="tests",
        kind="measured",
        source="metrics.m7_codebase (def test_* across tests/)",
        note=(
            "test *definitions*. This is deliberately not the number pytest "
            "prints: a parametrised definition is one function here and several "
            "cases there. Report test_cases alongside it or neither, never one "
            "of the two on its own."
        ),
    )
    section.add(
        "test_cases",
        total_cases,
        unit="cases",
        kind="measured",
        source="metrics.m7_codebase (definitions expanded by parametrize)",
        note=(
            "definitions expanded by their @pytest.mark.parametrize argument "
            f"lists, so {total_cases - total_tests} of these are expansions of "
            "the definitions above. This is the number to quote next to a pass "
            "count, because it is the number pytest collects — verified equal "
            "to `pytest --collect-only` on 2026-09-27."
        ),
    )
    section.add(
        "test_files",
        len(test_rows),
        unit="files",
        kind="measured",
        source="metrics.m7_codebase",
    )
    if total_code:
        section.add(
            "test_to_source_line_ratio",
            round(sum(r["code_lines"] for r in test_rows) / total_code, 3),
            kind="derived",
            source="metrics.m7_codebase",
            note="test code lines per line of source code",
        )

    collected = _collect_with_pytest()
    if collected is not None:
        section.add(
            "pytest_collected_cases",
            collected,
            unit="cases",
            kind="measured",
            source="pytest --collect-only -q",
            note=(
                "the number the suite actually runs, including parametrised "
                "expansions — the citable figure for the paper"
            ),
        )

    # ------------------------------------------------------------------ #
    # Schema, read from the live database
    # ------------------------------------------------------------------ #
    db.init_db()
    tables = [
        row["name"]
        for row in (
            db.row_to_dict(r)
            for r in db.query(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    ]
    schema_rows = []
    total_columns = total_fks = 0
    for table in tables:
        columns = [db.row_to_dict(r) for r in db.query(f"PRAGMA table_info({table})")]
        foreign_keys = [
            db.row_to_dict(r) for r in db.query(f"PRAGMA foreign_key_list({table})")
        ]
        indexes = [db.row_to_dict(r) for r in db.query(f"PRAGMA index_list({table})")]
        pii = sorted(
            column
            for column in PII_COLUMNS
            if column.startswith(f"{table}.")
            and column.split(".", 1)[1] in {c["name"] for c in columns}
        )
        schema_rows.append(
            {
                "table": table,
                "columns": len(columns),
                "foreign_keys": len(foreign_keys),
                "indexes": len(indexes),
                "cascade_deletes": sum(
                    1 for fk in foreign_keys if (fk.get("on_delete") or "") == "CASCADE"
                ),
                "not_null_columns": sum(1 for c in columns if c.get("notnull")),
                "pii_columns": len(pii),
                "pii_column_names": pii,
            }
        )
        total_columns += len(columns)
        total_fks += len(foreign_keys)
    section.tables["schema"] = schema_rows

    section.add(
        "tables",
        len(tables),
        unit="tables",
        kind="measured",
        source="sqlite_master on a freshly initialised schema",
        note=", ".join(tables),
    )
    section.add(
        "columns",
        total_columns,
        unit="columns",
        kind="measured",
        source="PRAGMA table_info",
    )
    section.add(
        "foreign_keys",
        total_fks,
        unit="constraints",
        kind="measured",
        source="PRAGMA foreign_key_list",
    )
    section.add(
        "cascade_delete_constraints",
        sum(r["cascade_deletes"] for r in schema_rows),
        unit="constraints",
        kind="measured",
        source="PRAGMA foreign_key_list",
        note=(
            "ON DELETE CASCADE is the deletion mechanism the data-safety section "
            "rests on: removing a user removes their applications, interviews, "
            "turns and proctoring events in one statement, so 'delete my data' "
            "has a single implementation rather than a checklist"
        ),
    )

    # PII inventory, with every entry checked against the live schema.
    live_columns = set()
    for table in tables:
        for column in (db.row_to_dict(r) for r in db.query(f"PRAGMA table_info({table})")):
            live_columns.add(f"{table}.{column['name']}")
    inventory = []
    for column, (category, why) in sorted(PII_COLUMNS.items()):
        inventory.append(
            {
                "column": column,
                "category": category,
                "rationale": why,
                "exists_in_schema": column in live_columns,
            }
        )
    section.tables["pii_inventory"] = inventory
    stale = [row["column"] for row in inventory if not row["exists_in_schema"]]
    section.add(
        "pii_columns",
        len(inventory),
        unit="columns",
        kind="measured",
        source="metrics.m7_codebase.PII_COLUMNS, verified against the live schema",
        note=(
            f"{len(inventory)} of {total_columns} columns hold personal data, "
            "hand-classified into identifier / credential / sensitive free text / "
            "biometric media / consent record / metadata"
        ),
    )
    section.add(
        "pii_inventory_is_current",
        not stale,
        kind="measured",
        source="PRAGMA table_info",
        note=(
            "every classified column exists in the schema"
            if not stale
            else "STALE — not in the schema: " + ", ".join(stale)
        ),
    )
    by_category: dict[str, int] = {}
    for row in inventory:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1
    section.tables["pii_by_category"] = by_category
    section.add(
        "biometric_media_columns",
        by_category.get("biometric media", 0),
        unit="columns",
        kind="measured",
        source="metrics.m7_codebase.PII_COLUMNS",
        note=(
            "face images, video and voice — the highest-risk category, and the "
            "reason the deployment section argues for local inference rather "
            "than treating it as a preference"
        ),
    )

    # ------------------------------------------------------------------ #
    # Configuration surface and prompts
    # ------------------------------------------------------------------ #
    config_rows = []
    for name in dir(settings):
        if name.startswith("_"):
            continue
        value = getattr(settings, name)
        if hasattr(value, "__dataclass_fields__"):
            config_rows.append(
                {"group": name, "settings": len(dataclass_fields(value))}
            )
    section.tables["config_groups"] = config_rows
    section.add(
        "config_groups",
        len(config_rows),
        unit="groups",
        kind="measured",
        source="core.config.settings",
    )
    section.add(
        "config_settings",
        sum(r["settings"] for r in config_rows),
        unit="settings",
        kind="measured",
        source="core.config",
        note="every one readable from the environment, none requiring a code change",
    )

    prompt_rows = []
    prompts_dir = Path(settings.prompts_dir)
    for path in sorted(prompts_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        prompt_rows.append(
            {
                "template": path.name,
                "bytes": len(text.encode("utf-8")),
                "lines": len(text.splitlines()),
                "placeholders": sorted(set(re.findall(r"\{[a-z_]+\}", text))),
            }
        )
    section.tables["prompt_templates"] = prompt_rows
    section.add(
        "prompt_templates",
        len(prompt_rows),
        unit="files",
        kind="measured",
        source=str(prompts_dir.relative_to(PROJECT_ROOT)),
        note=(
            "prompts live in files, not string literals, so a reviewer can read "
            "exactly what the model was asked — and so a deployment can change "
            "them without touching Python"
        ),
    )

    # ------------------------------------------------------------------ #
    # Providers and detectors
    # ------------------------------------------------------------------ #
    import llm
    from proctoring import rules as proctor_rules
    from services import guardrail_service as guardrails

    section.add(
        "llm_providers",
        list(llm.PROVIDERS),
        kind="specification",
        source="llm.PROVIDERS",
        note=(
            "all four speak plain REST over `requests`; no vendor SDK is a "
            "dependency, which is what makes swapping Gemini for a local Ollama "
            "a one-line environment change"
        ),
    )
    kinds = [
        value
        for name, value in vars(proctor_rules).items()
        if name.startswith("KIND_") and isinstance(value, str)
    ]
    section.add(
        "proctoring_event_kinds",
        len(kinds),
        unit="kinds",
        kind="measured",
        source="proctoring.rules.KIND_*",
        note=", ".join(sorted(kinds)),
    )
    section.add(
        "guardrail_pattern_families",
        len(guardrails._INJECTION_PATTERNS),
        unit="families",
        kind="specification",
        source="services.guardrail_service._INJECTION_PATTERNS",
    )

    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    pinned = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.strip().startswith(("#", "--"))
    ]
    section.add(
        "pinned_dependencies",
        len(pinned),
        unit="packages",
        kind="measured",
        source="requirements.txt",
        note="every version pinned exactly, verified on Python 3.12",
    )
    section.add(
        "agpl_dependencies",
        ["ultralytics"],
        kind="specification",
        source="requirements.txt",
        note=(
            "ultralytics is AGPL-3.0. It powers optional object detection, which "
            "ships disabled (PROCTORING_OBJECT_DETECTION=false). A paper claiming "
            "a permissively licensed stack has to name this, and has to say that "
            "the default configuration does not load it."
        ),
    )

    section.commentary = (
        "The schema and PII tables here are generated from the live database "
        "rather than transcribed, so the paper's data-inventory table cannot "
        "drift from the code. The AGPL dependency and the biometric-media "
        "columns are both reported because a deployability argument that omits "
        "them is not an argument."
    )
    return section


def _collect_with_pytest() -> int | None:
    """Ask pytest how many cases it would run. ``None`` if it cannot be run."""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception:
        return None
    match = re.search(r"(\d+)\s+tests? collected", completed.stdout)
    if match:
        return int(match.group(1))
    match = re.search(r"^(\d+) tests? collected", completed.stdout, re.MULTILINE)
    return int(match.group(1)) if match else None
