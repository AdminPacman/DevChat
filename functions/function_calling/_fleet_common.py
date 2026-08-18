"""Shared subprocess seam for fleet agent CLIs (pi / kimi / claude).

House module, 0018.05.26 a₿ — Pac's Arcade crew console.

Underscore-prefixed ON PURPOSE: `utils/function_manager.py` skips files whose name starts
with "_", so nothing in here is registered as a callable graph tool. This is plumbing only;
the actual tools live in `pi_agent.py` and `fleet_agents.py`.

WHY THIS EXISTS — the 0018.05.26 /bb post-mortem
------------------------------------------------
The first two `bb_showcard_v1` runs both "succeeded" (`ok: true`, exit 0) and wrote NOTHING.
Three separate defects, all of which this module exists to prevent recurring:

1. **The raw session stream got returned into graph context.** `pi_run` handed back 1.96 MB of
   Pi's JSONL transcript as its `output`. That value then rode the graph edge into
   QA_Verifier, whose own log entry ballooned to 2.8 MB. An agent CLI's session log is a
   DEBUG ARTIFACT, not a tool return value. `summarize_stream()` below reduces it to the
   handful of facts a downstream node can actually act on, and `persist_raw()` writes the
   full stream to disk so nothing is lost for forensics.

2. **`ok: true` meant "the process exited 0", which is not the same as "the work got done".**
   Pi exited clean having called `read` twice and `write` zero times. Any node routing on
   `ok` was routing on a lie. `snapshot_tree()` + `diff_tree()` give us ground truth — what
   files actually changed on disk — so `ok` can mean something.

3. **The model was thinking in a 4096-token window.** Ollama's server default `num_ctx` is
   4096 regardless of what the model card advertises (qwen3:4b claims 262144) or what the
   client claims (pi's models.json said 32000 — the OpenAI-compat `/v1` endpoint does not let
   the client set it). Pi read a 21 KB file, blew the window, lost the original instruction,
   and started "completing" the last thing it had seen. Fixed OUTSIDE this module by deriving
   `qwen3:4b-16k` (num_ctx 16384, verified 100% GPU-resident at 5.1 GB) — see the devchat
   skill. Recorded here because the symptom looked like a tool-calling failure and was not.

SECURITY — read before adding a new agent
-----------------------------------------
`scrub_or_refuse()` is a REFUSE gate, not a redactor. It fails the call loudly rather than
silently sending a maybe-secret onward, because a silent redaction that misses is worse than
a blocked call. It is a backstop for accidents, NOT a license to route sensitive material
near an external agent — the operating rule remains that secrets, wallet material, and infra
topology never enter an agent prompt in the first place.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Hard cap on any text we hand back into graph context. Node context windows are small
# (a local 4B model at 16k ctx is ~64 KB of text TOTAL for system + history + tools), so a
# tool return has to stay well under that or it evicts the conversation it was meant to serve.
MAX_RETURN_CHARS = 4000

# How much of a single captured field (final message, stderr) we keep before eliding.
MAX_FIELD_CHARS = 1500

# Files we never count as "produced work" when diffing a workspace.
_IGNORED_TREE_NAMES = {".git", "__pycache__", "node_modules", ".pi", ".DS_Store"}


# --------------------------------------------------------------------------------------
# Secret refusal
# --------------------------------------------------------------------------------------

# High-confidence secret shapes only. False positives block real work, so this list stays
# deliberately narrow: each entry is a format that has essentially no innocent meaning in a
# task description. Broad heuristics ("contains the word password") are NOT used.
_SECRET_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("nostr private key (nsec)", re.compile(r"\bnsec1[02-9ac-hj-np-z]{20,}", re.I)),
    ("bitcoin extended private key", re.compile(r"\b(xprv|yprv|zprv|tprv)[1-9A-HJ-NP-Za-km-z]{50,}")),
    ("lightning/NWC connection secret", re.compile(r"nostr\+walletconnect://", re.I)),
    ("LN node macaroon or admin key", re.compile(r"\b[0-9a-f]{64,}\.macaroon\b", re.I)),
    ("PEM private key block", re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----")),
    ("OpenSSH private key block", re.compile(r"-----BEGIN OPENSSH PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("OpenAI-style secret key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("Anthropic secret key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Moonshot/Kimi key", re.compile(r"\bsk-[A-Za-z0-9]{20,}", re.I)),
    ("BIP39 seed phrase (12+ words)", re.compile(
        r"\b(?:[a-z]{3,8}\s+){11,}[a-z]{3,8}\b(?=[^\w]*(?:seed|mnemonic|recovery|phrase|wallet))", re.I)),
)

# Fleet-specific material that must not travel to a third-party-hosted agent.
_FLEET_SENSITIVE: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("live SSN session id", re.compile(r"\bsession(?:id)?\s*[=:]\s*[A-Za-z0-9]{12,}", re.I)),
    ("private relay hostname", re.compile(r"relay\.pacsarcade\.org", re.I)),
    ("dotenv file contents", re.compile(r"^\s*(API_KEY|OPENAI_API_KEY|MOONSHOT_API_KEY)\s*=\s*\S+", re.M)),
)


def scrub_or_refuse(text: str, *, external: bool) -> Optional[str]:
    """Return a refusal reason if `text` looks like it carries secret material, else None.

    Args:
        text: the prompt/task string about to be handed to an agent subprocess.
        external: True when the receiving agent is a third-party-hosted service whose
            operator may train on or retain the content (Kimi/Moonshot). External callers
            additionally refuse on fleet infra topology, not just on universal secret shapes.

    This is intentionally a hard refusal. A tool that silently strips what it *thinks* is a
    secret will eventually strip the wrong 90% and send the other 10%.
    """
    checks: Iterable[Tuple[str, "re.Pattern[str]"]] = _SECRET_PATTERNS
    if external:
        checks = tuple(_SECRET_PATTERNS) + _FLEET_SENSITIVE

    for label, pattern in checks:
        if pattern.search(text):
            return (
                f"REFUSED: the task string appears to contain {label}. Secrets, wallet "
                "material, and infra topology are never sent to an agent subprocess. Remove "
                "it from the prompt and pass a reference (a file path the agent may read "
                "locally, or a named credential the operator supplies out of band) instead."
            )
    return None


# --------------------------------------------------------------------------------------
# Workspace ground truth
# --------------------------------------------------------------------------------------

def snapshot_tree(root: Path) -> Dict[str, Tuple[int, float]]:
    """Map every file under `root` to (size, mtime). Cheap stat-only walk, no hashing.

    Used before/after an agent subprocess so we can report what it ACTUALLY wrote rather
    than trusting its exit code (defect 2 in this module's docstring).
    """
    snap: Dict[str, Tuple[int, float]] = {}
    if not root.is_dir():
        return snap
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_TREE_NAMES]
        for fn in filenames:
            if fn in _IGNORED_TREE_NAMES:
                continue
            full = Path(dirpath) / fn
            try:
                st = full.stat()
            except OSError:
                continue
            try:
                rel = str(full.relative_to(root))
            except ValueError:
                continue
            snap[rel] = (st.st_size, st.st_mtime)
    return snap


def diff_tree(
    before: Dict[str, Tuple[int, float]],
    after: Dict[str, Tuple[int, float]],
) -> Dict[str, List[str]]:
    """Return {"created": [...], "modified": [...], "deleted": [...]}, each sorted."""
    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(p for p in (set(before) & set(after)) if before[p] != after[p])
    return {"created": created, "modified": modified, "deleted": deleted}


# --------------------------------------------------------------------------------------
# Stream compaction
# --------------------------------------------------------------------------------------

def _elide(text: str, limit: int = MAX_FIELD_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    keep = limit // 2
    dropped = len(text) - (keep * 2)
    return f"{text[:keep]}\n…[{dropped} chars elided]…\n{text[-keep:]}"


def summarize_stream(raw: str) -> Dict[str, Any]:
    """Reduce an agent CLI's stdout to the few facts a downstream graph node can use.

    Handles both shapes we actually see in the fleet:
      * JSONL event streams (pi `--mode json`, kimi/claude `--output-format stream-json`)
      * plain prose (kimi/claude `--output-format text`)

    Returns a dict with `tool_calls`, `final_text`, `usage`, and `stream_events`. Never
    returns the raw stream — that is `persist_raw()`'s job.
    """
    lines = [ln for ln in (raw or "").splitlines() if ln.strip()]
    events: List[Dict[str, Any]] = []
    for ln in lines:
        stripped = ln.strip()
        if not stripped.startswith("{"):
            continue
        try:
            obj = json.loads(stripped)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            events.append(obj)

    # Plain-text mode (or an unparseable stream): treat the whole thing as the final answer.
    if not events:
        return {
            "format": "text",
            "tool_calls": [],
            "final_text": _elide(raw.strip()),
            "usage": None,
            "stream_events": 0,
        }

    tool_calls: List[Dict[str, Any]] = []
    final_text = ""
    usage: Optional[Dict[str, Any]] = None

    for obj in events:
        etype = obj.get("type")

        # kimi's shape: role-keyed, content is usually a bare string.
        # e.g. {"role":"assistant","content":"pong"} / {"role":"meta","type":"system.version"}
        role = obj.get("role")
        if role == "assistant":
            # kimi emits OpenAI-style tool_calls on the assistant turn, and the final prose
            # as a separate assistant turn with a string `content`.
            for tc in obj.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                tool_calls.append({
                    "tool": fn.get("name") or tc.get("name"),
                    "args": _elide(str(fn.get("arguments") or tc.get("arguments") or ""), 300),
                })
            content = obj.get("content")
            if isinstance(content, str) and content.strip():
                final_text = content
            continue
        elif role == "tool":
            # This is the tool RESULT, not the call — the call was already recorded above.
            continue
        elif role == "meta":
            # system.version / session.resume_hint — bookkeeping, never work product.
            continue

        # pi's shape
        if etype == "tool_execution_start":
            tool_calls.append({
                "tool": obj.get("toolName"),
                "args": _elide(json.dumps(obj.get("args", {}), default=str), 300),
            })
        # kimi/claude stream-json shape: tool_use blocks nested in assistant messages
        elif etype in {"assistant", "message_end", "turn_end", "result"}:
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
            for block in (msg.get("content") or []) if isinstance(msg, dict) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tool_calls.append({
                        "tool": block.get("name"),
                        "args": _elide(json.dumps(block.get("input", {}), default=str), 300),
                    })
                elif block.get("type") == "text" and block.get("text"):
                    final_text = block["text"]
            if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
                usage = msg["usage"]
            if isinstance(obj.get("result"), str) and obj["result"].strip():
                final_text = obj["result"]

    return {
        "format": "jsonl",
        "tool_calls": tool_calls,
        "final_text": _elide(final_text.strip()),
        "usage": usage,
        "stream_events": len(events),
    }


def persist_raw(workspace: Path, label: str, raw: str) -> Optional[str]:
    """Write the full raw stream next to the run so forensics survive compaction.

    Returns the path as a string, or None if it could not be written (never raises — losing
    a debug artifact must not fail an otherwise-good tool call).
    """
    if not raw:
        return None
    try:
        logs_dir = workspace / "_agent_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        # Deterministic-ish unique name without importing time: count existing siblings.
        n = len(list(logs_dir.glob(f"{label}-*.log"))) + 1
        target = logs_dir / f"{label}-{n:03d}.log"
        target.write_text(raw, encoding="utf-8", errors="replace")
        return str(target)
    except OSError:
        return None


# --------------------------------------------------------------------------------------
# Binary resolution + the run itself
# --------------------------------------------------------------------------------------

def resolve_binary(name: str, env_override: str, fallbacks: Iterable[str]) -> Optional[str]:
    """Find an agent CLI: $ENV_OVERRIDE, then PATH, then known house install paths."""
    override = os.environ.get(env_override)
    if override and Path(override).is_file():
        return override
    found = shutil.which(name)
    if found:
        return found
    for candidate in fallbacks:
        if Path(candidate).is_file():
            return candidate
    return None


def resolve_workspace(context: Optional[Dict[str, Any]], tool_name: str) -> Path:
    """Hard-scope an agent to the calling node's run workspace.

    Same scoping `file.py`'s tools use. Agent CLIs run with the full permissions of the
    launching process and have no built-in sandbox, so this is the only thing standing
    between a graph node and the rest of the disk. Containerise before trusting any of
    these with more than a throwaway workspace.
    """
    if not context or not context.get("python_workspace_root"):
        raise ValueError(
            f"{tool_name} requires _context.python_workspace_root (the same workspace file.py "
            "tools are scoped to); this tool must be called from a graph node, not standalone."
        )
    path = Path(context["python_workspace_root"]).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_agent(
    *,
    label: str,
    cmd: List[str],
    workspace: Path,
    timeout_seconds: int,
    expect_files: bool,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run an agent CLI as a scoped subprocess and return a COMPACT, HONEST result.

    "Honest" is the operative word: `ok` is True only if the process exited 0 AND — when
    `expect_files` is set — the workspace actually changed. See defect 2 in this module's
    docstring for why exit code alone is not trustworthy.

    The full stdout is persisted to `<workspace>/_agent_logs/` and referenced by path; only
    the summary crosses back into graph context.
    """
    before = snapshot_tree(workspace)

    run_env = dict(os.environ)
    if env:
        run_env.update(env)

    try:
        completed = subprocess.run(
            cmd,
            cwd=str(workspace),
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=run_env,
        )
        raw_out = completed.stdout or ""
        raw_err = completed.stderr or ""
        returncode: Optional[int] = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        raw_out = exc.stdout if isinstance(exc.stdout, str) else ""
        raw_err = exc.stderr if isinstance(exc.stderr, str) else ""
        returncode = None
        timed_out = True
    except OSError as exc:
        return {
            "ok": False,
            "agent": label,
            "error": f"failed to launch {label} subprocess: {exc}",
            "returncode": None,
            "files": {"created": [], "modified": [], "deleted": []},
            "tool_calls": [],
            "final_text": "",
            "workspace": str(workspace),
            "raw_log": None,
        }

    after = snapshot_tree(workspace)
    files = diff_tree(before, after)
    # An agent's own debug log is not "work produced" — don't let it fake a successful diff.
    for key in files:
        files[key] = [p for p in files[key] if not p.startswith("_agent_logs" + os.sep)]

    summary = summarize_stream(raw_out)
    raw_path = persist_raw(workspace, label, raw_out)

    wrote_something = bool(files["created"] or files["modified"])
    exited_clean = returncode == 0

    if timed_out:
        error: Optional[str] = f"{label} timed out after {timeout_seconds}s"
    elif not exited_clean:
        error = _elide(raw_err.strip() or f"{label} exited {returncode}", 600)
    elif expect_files and not wrote_something:
        # THE /bb FAILURE MODE, now caught instead of reported as success.
        error = (
            f"{label} exited 0 but wrote no files to the workspace. It made "
            f"{len(summary['tool_calls'])} tool call(s): "
            f"{', '.join(tc['tool'] or '?' for tc in summary['tool_calls']) or 'none'}. "
            "The work was NOT done — treat this as a failure, not a pass."
        )
    else:
        error = None

    result: Dict[str, Any] = {
        "ok": error is None,
        "agent": label,
        "error": error,
        "returncode": returncode,
        "files": files,
        "tool_calls": summary["tool_calls"][:20],
        "final_text": summary["final_text"],
        "usage": summary.get("usage"),
        "workspace": str(workspace),
        "raw_log": raw_path,
    }

    # Final belt-and-braces cap: no tool return may blow a node's context window.
    blob = json.dumps(result, default=str)
    if len(blob) > MAX_RETURN_CHARS:
        result["tool_calls"] = result["tool_calls"][:5]
        result["final_text"] = _elide(result["final_text"], 800)
        result["truncated"] = (
            f"result compacted to fit node context; full stream at {raw_path or 'unavailable'}"
        )
    return result
