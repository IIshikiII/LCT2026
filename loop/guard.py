"""PreToolUse hook. Blocks dangerous shell commands during the autonomous loop.

Claude Code runs this before every Bash / PowerShell call and feeds the tool
input as JSON on stdin. Exit code 2 blocks the call and shows stderr to the
model. Any other exit code lets the call through.

The loop runs with permissions bypassed, so this file is the only thing between
the agent and an irreversible action. Keep every rule narrow and explain the
refusal: the model reads the message and must be able to pick another way.

ASCII only. The file is read by a Windows shell that may not be UTF-8.
"""

from __future__ import annotations

import json
import re
import sys

# (pattern, reason). The pattern runs against the whole command line, case
# insensitive. Order does not matter: the first match wins.
RULES: list[tuple[str, str]] = [
    # --- remote repository -------------------------------------------------
    (r"\bgit\s+push\b", "git push is forbidden in the loop. Commit locally only."),
    (r"\bgit\s+remote\s+(add|set-url|remove|rename)\b", "Do not change git remotes."),
    (r"\bgh\s+(pr|release|repo|api)\b", "Do not talk to GitHub. Work stays local."),
    # --- branch protection -------------------------------------------------
    (
        r"\bgit\s+(checkout|switch)\s+(-\s*)?(main|master|backend)\b",
        "Stay on branch test_loop. Other branches are off limits.",
    ),
    (r"\bgit\s+branch\s+-[dD]\b", "Do not delete branches."),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard throws work away. Use git revert."),
    (r"\bgit\s+clean\s+-[a-z]*[fd]", "git clean deletes untracked files. Not allowed."),
    (r"\bgit\s+rebase\b", "Do not rewrite history. Use plain commits."),
    (r"\bgit\s+filter-|\bgit\s+reflog\s+expire\b", "Do not rewrite history."),
    # --- data that cannot be regenerated -----------------------------------
    (
        r"(rm|del|Remove-Item)\b[^\n]*\b(raw_task|eda[/\\]out|\.venv|node_modules)\b",
        "These paths hold source data or installed environments. Do not delete them.",
    ),
    (
        r"(rm|Remove-Item)\b[^\n]*(-rf|-Recurse)[^\n]*\s(/|[A-Za-z]:[/\\]|~)",
        "Recursive delete outside the repository is forbidden.",
    ),
    (r"\bgit\s+rm\b[^\n]*\b(raw_task|eda)\b", "Do not remove source data from git."),
    # --- containers and volumes --------------------------------------------
    (
        r"docker\s+compose[^\n]*\bdown\b[^\n]*(-v|--volumes)",
        "This drops the database volume. Use docker compose restart db.",
    ),
    (r"docker\s+(volume\s+rm|system\s+prune)", "Do not prune docker state."),
    # --- publishing ---------------------------------------------------------
    (r"\bnpm\s+publish\b|\btwine\s+upload\b", "Do not publish packages."),
    # --- machine ------------------------------------------------------------
    (r"\b(shutdown|Restart-Computer|Stop-Computer)\b", "Do not touch the machine."),
    (r"\breg\s+delete\b|\bSet-ItemProperty\s+HK", "Do not touch the registry."),
    (r"\b(format|diskpart|mkfs)\b", "Do not touch disks."),
    # --- remote code ---------------------------------------------------------
    (
        r"(curl|wget|iwr|Invoke-WebRequest)[^\n]*\|\s*(sh|bash|pwsh|powershell|python)",
        "Do not pipe downloaded content into a shell.",
    ),
    # --- sending files out ---------------------------------------------------
    # Matters once the web tools are on: a page can carry instructions, and an
    # unattended agent might follow them. These rules stop the plain ways of
    # posting a file to a remote host. They reduce an accident, they do not
    # stop a determined attack.
    (
        r"\bcurl\b[^\n]*(-T\s|--upload-file|-F\s+\S*@|(--data-binary|--data|-d)\s+@)",
        "Do not upload files to a remote host.",
    ),
    (
        r"(Invoke-WebRequest|Invoke-RestMethod|iwr|irm)[^\n]*-InFile\b",
        "Do not upload files to a remote host.",
    ),
    (
        r"(scp|rsync|sftp)\s+[^\n]*@[^\n]*:",
        "Do not copy files to another machine.",
    ),
    (
        r"\bnc\b[^\n]*<\s*\S|\bncat\b[^\n]*<\s*\S",
        "Do not send file contents over a raw socket.",
    ),
    # --- credentials ---------------------------------------------------------
    (
        r"\.env\b[^\n]*\|\s*(curl|wget|nc)|\b(cat|type)\b[^\n]*\.ssh[/\\]id_",
        "Do not read or send secrets.",
    ),
]

COMPILED = [(re.compile(pattern, re.IGNORECASE), reason) for pattern, reason in RULES]


def command_of(payload: dict) -> str:
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return ""
    parts = [tool_input.get("command"), tool_input.get("script")]
    return "\n".join(str(part) for part in parts if part)


def main() -> int:
    try:
        # Read bytes and decode with utf-8-sig. PowerShell puts a byte order
        # mark in front of anything it pipes into a native program, and
        # json.loads refuses one.
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace").strip()
        payload = json.loads(raw or "{}")
    except Exception:  # noqa: BLE001
        # A hook that cannot read its input must not block work.
        return 0

    if payload.get("tool_name") not in {"Bash", "PowerShell"}:
        return 0

    command = command_of(payload)
    if not command:
        return 0

    for pattern, reason in COMPILED:
        if pattern.search(command):
            sys.stderr.write(
                "Blocked by loop/guard.py: " + reason + "\n"
                "Rule: " + pattern.pattern + "\n"
                "Pick another way or write the reason into loop/JOURNAL.md.\n"
            )
            return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
