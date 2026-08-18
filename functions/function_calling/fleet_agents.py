"""Pac's Arcade crew tools — `kimi_run` and `claude_run` as DevChat function-calling seams.

House module, 0018.05.26 a₿. Companion to `pi_agent.py` (`pi_run`); all three share the
plumbing in `_fleet_common.py`.

THE POINT
---------
DevChat is the choreographer: it owns topology, role sequencing, QA gates, loop bounds, and
the `type: human` node where Pac approves. What it has historically lacked is capable HANDS
and capable JUDGMENT — a local 4B model is neither. These tools let one graph route work to
whichever crew member actually fits the step, and — because every one of them is a subprocess
of the run — the whole thing stays visible in ONE console instead of three terminals.

  pi_run      → the hands. Read/Write/Edit/Bash against the run workspace, local models.
  kimi_run    → the guest builder. Big design/build arcs. THIRD-PARTY HOSTED (see below).
  claude_run  → coordination, specs, gate reasoning. First-party, local subscription.
  (agent nodes) → cheap bulk reasoning on local Ollama models.
  (human node)  → Pac. The approval that makes the rest safe to run unattended.

🔒 KIMI IS EXTERNAL — the guardrail that is not negotiable
----------------------------------------------------------
Moonshot hosts Kimi and may train on or retain what is sent. Two enforcements live HERE, in
the tool, rather than in a prompt, because a prompt-level rule is one careless graph author
away from being ignored:

  1. `--skills-dir` is PINNED to the curated bundle (`~/dev/kimi-code-skills/skills`). That
     flag REPLACES kimi's auto-discovery of user/project skills (verified against
     `kimi --help`, 0.36.1), so pinning it is what stops the full fleet skill set — which
     documents private infra — from being handed to a third party. It is not an argument a
     caller may override.
  2. Every task string is run through `scrub_or_refuse(..., external=True)` and the call is
     REFUSED, loudly, if it carries secret or infra-topology shapes. This is a backstop for
     accidents, not permission to route sensitive work through Kimi.

Treat every `kimi_run` call as though its contents will be published, because in the ways
that matter, they may be.

HUMAN-NODE PLACEMENT
--------------------
Any graph that can write to a real repo, publish, or touch money gets a `type: human` node
before the irreversible step. Human nodes only block properly in live web-UI/WebSocket runs,
so crew graphs are LAUNCHED FROM THE CONSOLE, never by curl. Unattended runs use the
outbox→gate hard stop instead. Generated output stays UNTRUSTED until the gold-in-gold-out
gate; no graph writes directly into a fleet repo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

# Full package path, matching the house import style in `deep_research.py`. The loader in
# `utils/function_manager.py` imports each tool file under a synthetic module name, so a bare
# sibling import (`from _fleet_common import …`) would not resolve — the repo root is what is
# on sys.path, not this directory.
from functions.function_calling._fleet_common import (
    resolve_binary,
    resolve_workspace,
    run_agent,
    scrub_or_refuse,
)

# PINNED, not configurable. See the module docstring's guardrail 1.
CURATED_KIMI_SKILLS_DIR = "/home/pac/dev/kimi-code-skills/skills"

_KIMI_FALLBACKS = ("/home/pac/.npm-global/bin/kimi",)
_CLAUDE_FALLBACKS = ("/home/pac/.local/bin/claude",)

# claude -p otherwise inherits the operator's whole environment — every MCP server the
# desktop session has connected (mail, calendar, drive, browser). A graph node has no
# business holding those. Restrict to the file/search set unless a caller opts out.
_CLAUDE_DEFAULT_TOOLS = "Read,Write,Edit,Grep,Glob"


def kimi_run(
    task: str,
    timeout_seconds: int = 900,
    expect_files: bool = True,
    _context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Delegate a design/build task to Kimi (K3) as a scoped subprocess.

    Kimi is the fleet's guest builder — strongest on large design and build arcs where taste
    and breadth matter more than surgical local edits. Runs headless (`kimi -p`) on the
    operator's existing subscription, cwd-locked to this node's run workspace, with the
    curated skills bundle pinned.

    ⚠️ EXTERNAL SERVICE. Moonshot hosts this model and may retain or train on what you send.
    Never put secrets, keys, wallet material, session ids, or private infra topology in
    `task` — the tool refuses obvious cases but cannot catch everything. Write the prompt as
    if it will be public.

    Args:
        task (str): The full instruction for Kimi. Kimi cannot see the DevChat graph's
            conversation — only this string plus whatever it reads from the workspace — so
            include enough context for it to act without asking. Reference files by
            workspace-relative path and let Kimi read them itself rather than pasting large
            file bodies into the prompt.
        timeout_seconds (int): Hard wall-clock cap. Kimi is network-bound and slower than a
            local call; a real build arc wants several minutes. Keep this under the node's
            own graph-level timeout so a hang fails loud instead of stalling the run.
        expect_files (bool): When True (default), the call is reported as FAILED if Kimi
            exits clean but changed nothing on disk. Set False only for genuinely
            advisory/analysis calls whose product is the returned text.

    Returns:
        dict: {"ok", "agent", "error", "returncode", "files": {created/modified/deleted},
               "tool_calls", "final_text", "usage", "workspace", "raw_log"}
        `ok` is honest: exit 0 alone does not earn it (see `_fleet_common`'s post-mortem).
        The full session stream is written to `<workspace>/_agent_logs/` and referenced by
        `raw_log`; only a compact summary crosses back into graph context.
    """
    workspace = resolve_workspace(_context, "kimi_run")

    refusal = scrub_or_refuse(task, external=True)
    if refusal:
        return {
            "ok": False,
            "agent": "kimi",
            "error": refusal,
            "returncode": None,
            "files": {"created": [], "modified": [], "deleted": []},
            "tool_calls": [],
            "final_text": "",
            "workspace": str(workspace),
            "raw_log": None,
        }

    kimi_bin = resolve_binary("kimi", "KIMI_BIN", _KIMI_FALLBACKS)
    if kimi_bin is None:
        return {
            "ok": False,
            "agent": "kimi",
            "error": (
                f"kimi CLI not found (checked $KIMI_BIN, PATH, {_KIMI_FALLBACKS[0]}). "
                "Install the Kimi CLI and re-run."
            ),
            "returncode": None,
            "files": {"created": [], "modified": [], "deleted": []},
            "tool_calls": [],
            "final_text": "",
            "workspace": str(workspace),
            "raw_log": None,
        }

    skills_dir = Path(CURATED_KIMI_SKILLS_DIR)
    if not skills_dir.is_dir():
        # Fail closed. Running without the pin would silently hand Kimi auto-discovered
        # fleet skills — the exact leak this guardrail exists to prevent.
        return {
            "ok": False,
            "agent": "kimi",
            "error": (
                f"curated skills bundle missing at {CURATED_KIMI_SKILLS_DIR}. Refusing to run "
                "kimi without the pinned --skills-dir, because auto-discovery would expose "
                "the full fleet skill set to a third-party-hosted model."
            ),
            "returncode": None,
            "files": {"created": [], "modified": [], "deleted": []},
            "tool_calls": [],
            "final_text": "",
            "workspace": str(workspace),
            "raw_log": None,
        }

    cmd = [
        kimi_bin,
        "-p", task,
        "--output-format", "stream-json",
        "--skills-dir", str(skills_dir),
        # NO permission flag here, deliberately. The CLI REJECTS both `--auto` and `-y/--yolo`
        # alongside `--prompt` ("Cannot combine --prompt with --auto", verified 0.36.1),
        # because `-p` already runs in auto permission mode — headless prompt mode has no
        # interactive channel to ask on. Adding either flag makes the call fail outright.
    ]

    return run_agent(
        label="kimi",
        cmd=cmd,
        workspace=workspace,
        timeout_seconds=timeout_seconds,
        expect_files=expect_files,
    )


def claude_run(
    task: str,
    timeout_seconds: int = 600,
    expect_files: bool = False,
    allowed_tools: str = _CLAUDE_DEFAULT_TOOLS,
    _context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Delegate coordination, spec-writing, or gate reasoning to Claude as a subprocess.

    Claude is first-party here (local CLI, operator's own subscription) and is the crew's
    strongest reader — use it for the steps where the job is to JUDGE rather than to type:
    reviewing another agent's output against a spec, writing the spec in the first place,
    or reconciling disagreement between two nodes.

    Args:
        task (str): The full instruction. Like the other crew tools, Claude sees only this
            string plus the workspace — not the graph conversation.
        timeout_seconds (int): Hard wall-clock cap on the subprocess.
        expect_files (bool): Defaults to False, because this tool's usual product is
            reasoning returned as text, not files. Set True when the call is genuinely
            expected to write something, so a silent no-op is caught.
        allowed_tools (str): Comma-separated tool allowlist passed through to the CLI.
            Defaults to the file/search set. This exists because a headless `claude -p`
            otherwise inherits every MCP server the operator's desktop session has connected
            — mail, calendar, drive, browser — none of which a graph node should hold.
            Widen deliberately, never casually.

    Returns:
        dict: same compact shape as `kimi_run`.
    """
    workspace = resolve_workspace(_context, "claude_run")

    # First-party, but the same discipline applies: prompts are not a place for secrets.
    refusal = scrub_or_refuse(task, external=False)
    if refusal:
        return {
            "ok": False,
            "agent": "claude",
            "error": refusal,
            "returncode": None,
            "files": {"created": [], "modified": [], "deleted": []},
            "tool_calls": [],
            "final_text": "",
            "workspace": str(workspace),
            "raw_log": None,
        }

    claude_bin = resolve_binary("claude", "CLAUDE_BIN", _CLAUDE_FALLBACKS)
    if claude_bin is None:
        return {
            "ok": False,
            "agent": "claude",
            "error": (
                f"claude CLI not found (checked $CLAUDE_BIN, PATH, {_CLAUDE_FALLBACKS[0]})."
            ),
            "returncode": None,
            "files": {"created": [], "modified": [], "deleted": []},
            "tool_calls": [],
            "final_text": "",
            "workspace": str(workspace),
            "raw_log": None,
        }

    cmd = [
        claude_bin,
        "-p", task,
        "--output-format", "stream-json",
        "--verbose",              # the CLI requires it alongside stream-json
        "--allowedTools", allowed_tools,
    ]

    return run_agent(
        label="claude",
        cmd=cmd,
        workspace=workspace,
        timeout_seconds=timeout_seconds,
        expect_files=expect_files,
    )
