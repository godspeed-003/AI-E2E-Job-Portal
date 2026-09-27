"""Turn a Claude Code session log into something a person can read.

Claude Code writes every session to a JSONL file under
``~/.claude/projects/<slugified-cwd>/<session-id>.jsonl``. One line per event:
user turns, assistant turns, thinking blocks, tool calls, tool results, and a
lot of housekeeping (titles, modes, queue operations) that nobody wants to read.
This script flattens one of those files into plain text.

    python scripts/export_chat.py                      # newest session, to a .txt
    python scripts/export_chat.py --list               # what sessions exist
    python scripts/export_chat.py --session 80c098d8   # a specific one (prefix is fine)
    python scripts/export_chat.py --all                # every session, one file each
    python scripts/export_chat.py --no-thinking        # drop reasoning blocks
    python scripts/export_chat.py --full               # do not truncate tool results

**Tool results are truncated by default** to 2000 characters. This is the
difference between a readable export and an unusable one: a single ``Read`` of a
large file or a ``git log`` dump can run to hundreds of kilobytes, and a handful
of them will bury every actual sentence in the conversation. ``--full`` keeps
everything if you are archiving rather than reading; the header of each export
records which mode produced it.

Nothing here is specific to this project — point ``--projects-dir`` somewhere
else and it will export any Claude Code session on the machine.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Housekeeping records. They carry no conversation, only UI and session state.
SKIP_TYPES = frozenset(
    {
        "custom-title",
        "ai-title",
        "mode",
        "atis-latch",
        "last-prompt",
        "queue-operation",
        "attachment",
        "summary",
    }
)

RULE = "=" * 78
THIN = "-" * 78


def _default_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def _project_dir(projects_dir: Path, cwd: Path) -> Path:
    """Find the log directory for a working directory.

    Claude Code slugifies the absolute path by replacing separators, colons and
    dots with dashes: ``C:\\Users\\a\\b`` becomes ``C--Users-a-b``. Rather than
    re-implement that and drift from it, match on the directory name.
    """
    slug = str(cwd)
    for char in "\\/:. ":
        slug = slug.replace(char, "-")
    candidate = projects_dir / slug
    if candidate.is_dir():
        return candidate

    # Fall back to the directory whose name ends with the project folder's name,
    # which survives small differences in how the prefix was slugified.
    tail = cwd.name.replace(".", "-")
    matches = [d for d in projects_dir.iterdir() if d.is_dir() and d.name.endswith(tail)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(
            f"No session logs for {cwd}\nLooked in: {projects_dir}\n"
            "Pass --projects-dir if your logs live somewhere else."
        )
    raise SystemExit(
        "Several log directories could match; pass one with --project-dir:\n  "
        + "\n  ".join(str(m) for m in matches)
    )


def _sessions(project_dir: Path) -> list[Path]:
    """Session files, oldest first."""
    return sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)


def _read(path: Path) -> list[dict]:
    events = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                # A session killed mid-write leaves a half-line at the end.
                # Losing it beats refusing to export the other 500 events.
                print(f"  ! {path.name} line {number} is not valid JSON; skipped")
    return events


def _stamp(event: dict) -> str:
    raw = event.get("timestamp")
    if not raw:
        return ""
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return raw
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _clip(text: str, limit: int | None) -> str:
    if limit is None or len(text) <= limit:
        return text
    dropped = len(text) - limit
    return f"{text[:limit]}\n... [{dropped:,} more characters truncated]"


def _blocks(content) -> list[dict]:
    """Normalise the two shapes message content comes in."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(f"[{item.get('type', 'content')}]")
        return "\n".join(parts)
    return ""


def render(events: list[dict], *, thinking: bool, limit: int | None) -> str:
    out: list[str] = []
    tool_names: dict[str, str] = {}  # tool_use_id -> name, to label results
    counts = {"user": 0, "assistant": 0, "tools": 0, "compactions": 0}

    for event in events:
        kind = event.get("type")
        if kind in SKIP_TYPES:
            continue

        if kind == "system":
            subtype = event.get("subtype")
            if subtype == "compact_boundary":
                meta = event.get("compactMetadata") or {}
                counts["compactions"] += 1
                out.append("")
                out.append(RULE)
                out.append(
                    "  CONVERSATION COMPACTED  "
                    f"({meta.get('preTokens', '?')} tokens -> "
                    f"{meta.get('postTokens', '?')}, trigger: "
                    f"{meta.get('trigger', '?')})"
                )
                out.append(
                    "  Everything above was replaced by a summary in the model's "
                    "context."
                )
                out.append(RULE)
                out.append("")
            elif subtype == "api_error":
                error = event.get("error") or {}
                message = error.get("message", "")
                out.append(f"[api error] {_clip(str(message), 300)}")
                out.append("")
            continue

        message = event.get("message")
        if not isinstance(message, dict):
            continue
        blocks = _blocks(message.get("content"))
        if not blocks:
            continue

        stamp = _stamp(event)

        if kind == "user":
            # A user event holds either a real typed message (string content) or
            # the tool results from the previous assistant turn.
            results = [b for b in blocks if b.get("type") == "tool_result"]
            spoken = [b for b in blocks if b.get("type") == "text"]

            for block in spoken:
                counts["user"] += 1
                out.append("")
                out.append(RULE)
                out.append(f"USER  [{stamp}]")
                out.append(RULE)
                out.append(block.get("text", "").rstrip())
                out.append("")

            for block in results:
                name = tool_names.get(block.get("tool_use_id", ""), "tool")
                flag = " (error)" if block.get("is_error") else ""
                body = _clip(_result_text(block).rstrip(), limit)
                out.append(f"  <- result from {name}{flag}:")
                out.append("\n".join(f"     {ln}" for ln in body.splitlines()))
                out.append("")

        elif kind == "assistant":
            for block in blocks:
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "").rstrip()
                    if not text:
                        continue
                    counts["assistant"] += 1
                    out.append("")
                    out.append(THIN)
                    out.append(f"CLAUDE  [{stamp}]")
                    out.append(THIN)
                    out.append(text)
                    out.append("")
                elif btype == "thinking" and thinking:
                    text = block.get("thinking", "").rstrip()
                    if not text:
                        continue
                    out.append("  [thinking]")
                    out.append("\n".join(f"  | {ln}" for ln in text.splitlines()))
                    out.append("")
                elif btype == "tool_use":
                    counts["tools"] += 1
                    name = block.get("name", "?")
                    tool_names[block.get("id", "")] = name
                    out.append(f"  -> {name}")
                    for key, value in (block.get("input") or {}).items():
                        rendered = (
                            value if isinstance(value, str) else json.dumps(value)
                        )
                        rendered = _clip(rendered, limit)
                        first, *rest = rendered.splitlines() or [""]
                        out.append(f"     {key}: {first}")
                        out.extend(f"       {ln}" for ln in rest)
                    out.append("")

    return "\n".join(out), counts


def header(path: Path, events: list[dict], counts: dict, *, thinking, limit) -> str:
    stamps = [_stamp(e) for e in events if e.get("timestamp")]
    title = next(
        (e.get("customTitle") or e.get("aiTitle") for e in events
         if e.get("type") in ("custom-title", "ai-title")),
        None,
    )
    cwd = next((e.get("cwd") for e in events if e.get("cwd")), "?")
    branch = next((e.get("gitBranch") for e in events if e.get("gitBranch")), "?")

    lines = [
        RULE,
        f"  {title or 'Claude Code session'}",
        RULE,
        f"  session   {path.stem}",
        f"  source    {path}",
        f"  project   {cwd}",
        f"  branch    {branch}",
        f"  spans     {stamps[0] if stamps else '?'}  ->  "
        f"{stamps[-1] if stamps else '?'}   (local time)",
        f"  contains  {counts['user']} user messages, "
        f"{counts['assistant']} replies, {counts['tools']} tool calls, "
        f"{counts['compactions']} compactions",
        f"  exported  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
        f"by scripts/export_chat.py",
        f"  settings  thinking={'kept' if thinking else 'dropped'}, "
        f"tool output={'full' if limit is None else f'clipped at {limit} chars'}",
        RULE,
        "",
    ]
    return "\n".join(lines)


def export(path: Path, out_path: Path, *, thinking: bool, limit: int | None) -> Path:
    events = _read(path)
    body, counts = render(events, thinking=thinking, limit=limit)
    out_path.write_text(
        header(path, events, counts, thinking=thinking, limit=limit) + body,
        encoding="utf-8",
    )
    size = out_path.stat().st_size
    print(
        f"  {out_path.name:<48} {size / 1024:>8,.0f} KB   "
        f"{counts['user']} msgs, {counts['tools']} tool calls"
    )
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="export_chat",
        description="Export a Claude Code session log to readable text.",
    )
    parser.add_argument("--session", help="session id or unique prefix of one")
    parser.add_argument("--all", action="store_true", help="export every session")
    parser.add_argument("--list", action="store_true", help="list sessions and exit")
    parser.add_argument("--out", type=Path, help="output file (single session only)")
    parser.add_argument(
        "--out-dir", type=Path, default=Path.cwd(), help="where to write (default: cwd)"
    )
    parser.add_argument(
        "--no-thinking", action="store_true", help="omit the model's reasoning blocks"
    )
    parser.add_argument(
        "--max-result",
        type=int,
        default=2000,
        metavar="N",
        help="clip tool output at N characters (default 2000)",
    )
    parser.add_argument(
        "--full", action="store_true", help="never clip tool output (large files)"
    )
    parser.add_argument("--projects-dir", type=Path, default=_default_projects_dir())
    parser.add_argument("--project-dir", type=Path, help="exact log directory to use")
    args = parser.parse_args(argv)

    project_dir = args.project_dir or _project_dir(args.projects_dir, Path.cwd())
    sessions = _sessions(project_dir)
    if not sessions:
        raise SystemExit(f"No .jsonl session logs in {project_dir}")

    if args.list:
        print(f"Sessions in {project_dir}:\n")
        for path in sessions:
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            print(
                f"  {path.stem}   {modified:%Y-%m-%d %H:%M}   "
                f"{path.stat().st_size / 1024:>8,.0f} KB"
            )
        return 0

    if args.session:
        chosen = [p for p in sessions if p.stem.startswith(args.session)]
        if not chosen:
            raise SystemExit(f"No session starts with {args.session!r}. Try --list.")
        if len(chosen) > 1:
            raise SystemExit(
                f"{args.session!r} matches several sessions:\n  "
                + "\n  ".join(p.stem for p in chosen)
            )
        targets = chosen
    elif args.all:
        targets = sessions
    else:
        targets = [sessions[-1]]  # newest, which is almost always "this chat"

    limit = None if args.full else args.max_result
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Exporting {len(targets)} session(s) from {project_dir}:")
    written = []
    for path in targets:
        if args.out and len(targets) == 1:
            out_path = args.out
        else:
            stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
            out_path = args.out_dir / f"chat-{stamp}-{path.stem[:8]}.txt"
        written.append(export(path, out_path, thinking=not args.no_thinking, limit=limit))

    print(f"\nWrote {len(written)} file(s) to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
