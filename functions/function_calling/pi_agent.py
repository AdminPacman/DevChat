"""Pi (earendil-works/pi, pi.dev) subprocess seam — "hands" for one DevChat agent node.

House addition, 0018.05.25 a₿ (devchat build agent, Job 1 of the /bb wiring task).

DevChat's own file tools (`file.py`, `code_executor.py`) remain the default and are NOT
removed or replaced by this module — this is purely additive. Wire `pi_run` onto a node's
`tooling` list (type: function) when that node's job is genuinely "edit real files in the
run's workspace" and you want Pi's mature Read/Write/Edit/Bash coding-agent loop doing that
edit instead of a bespoke save_file call. DevChat stays the graph choreographer (topology,
role sequencing, QA gates, loop bounds); Pi is scoped, single-purpose hands for this one call.

Pinned version: @earendil-works/pi-coding-agent 0.84.2 (npm, installed to
~/.npm-global, NOT vendored into this repo — see ~/dev/skills/pi/SKILL.md "Running it
locally"). Re-pin deliberately; do not let this drift to `pi update` without re-verifying
against ~/dev/skills/pi/SKILL.md's fast-moving-upstream warning.

Ollama wiring: Pi talks to the same local Ollama DevChat already uses via
~/.pi/agent/models.json (OpenAI-completions custom-provider path, provider id "ollama",
baseUrl http://localhost:11434/v1) — zero new inference-serving work, same models DevChat's
own `.env` MODEL_NAME points at.

Isolation note (carried over from the pi skill's risk list): Pi runs with the full
permissions of the launching process and has NO built-in sandboxing. This tool hard-scopes
Pi's cwd to the calling node's `python_workspace_root` (the same workspace file.py's tools
are scoped to) and does not accept an absolute/parent-escaping path — never point this at
anything but the run's own workspace. Containerize (podman) before trusting this with
anything beyond a throwaway workspace, per fleet law.
"""

import os
from typing import Any, Dict, Optional

# Shared plumbing with the other crew tools (`kimi_run`, `claude_run`). See
# `_fleet_common.py`'s docstring for the 0018.05.26 /bb post-mortem that motivated it:
# raw-stream-into-graph-context, dishonest `ok`, and the 4096-token window.
from functions.function_calling._fleet_common import (
    resolve_binary,
    resolve_workspace,
    run_agent,
)

_PI_BIN_FALLBACKS = (
    "/home/pac/.npm-global/bin/pi",
)

# Derived Ollama variant with num_ctx=16384 (verified 100% GPU-resident, 5.1 GB on the
# 4070 Ti SUPER). The stock `qwen3:4b` tag runs at Ollama's SERVER default of 4096 tokens
# regardless of the 262144 the model card advertises and the 32000 pi's models.json claims —
# the OpenAI-compat /v1 endpoint gives the client no way to raise it. Pi read one 21 KB
# reference file, blew the window, lost the instruction, and started completing the wrong
# task. That is the whole story of the first two /bb runs.
_DEFAULT_PI_MODEL = "qwen3:4b-16k"


def pi_run(
    task: str,
    model: str = _DEFAULT_PI_MODEL,
    provider: str = "ollama",
    timeout_seconds: int = 300,
    expect_files: bool = True,
    _context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Delegate a file-editing/coding task to Pi, running as a scoped subprocess "hands" call.

    Pi (earendil-works/pi) is a minimal coding-agent CLI with 4 built-in tools (Read, Write,
    Edit, Bash). This call runs Pi in non-interactive print/JSON mode, cwd-locked to this
    node's run workspace, against the same local Ollama models DevChat itself uses. Use this
    when a node needs a real file edit made and you want Pi's mature edit loop instead of
    DevChat's own save_file/read_text_file_snippet tools — not as a replacement for those
    tools, as an alternative "hands" for the specific node wired to use it.

    Args:
        task (str): The coding instruction for Pi to carry out (what to build/fix/edit in the
            workspace). Include enough context in this string for Pi to act without asking —
            Pi cannot see the rest of the DevChat graph's conversation, only what's passed here.
        model (str): Ollama model id to run Pi against. Must already be registered in
            ~/.pi/agent/models.json under the "ollama" provider. Defaults to the derived
            16k-context variant — do NOT pass the stock `qwen3:4b` tag for any task that
            involves reading real source files; at 4096 tokens it silently drops the
            instruction mid-run (see the module constant's note).
        provider (str): Which provider block in `~/.pi/agent/models.json` to use.
            `"ollama"` (default) = local, free, no egress. `"nvidia"` = NVIDIA NIM hosted
            models — **use this for any task that must read several real source files and
            then emit a large one.** A local 4B at 16k cannot both hold ~40 KB of references
            AND generate a 16 KB file; the hosted models have a real 128k window. Pi picks
            the credential up from `NVIDIA_API_KEY` in the environment (DevChat's
            `load_dotenv_file()` puts it there), so the key is never duplicated into
            models.json.
        timeout_seconds (int): Hard wall-clock cap on the Pi subprocess. Keep this well under
            the node's own graph-level timeout so a hung Pi call fails loud instead of hanging
            the whole run.
        expect_files (bool): When True (default), the call is reported as FAILED if Pi exits
            clean but changed nothing on disk. Pi is "hands" — a run that wrote no files did
            not do its job, however confident its closing summary sounds. Set False only for
            read-only/advisory calls.

    Returns:
        dict: {"ok", "agent", "error", "returncode", "files": {created/modified/deleted},
               "tool_calls", "final_text", "usage", "workspace", "raw_log"}

    `ok` is honest: exit 0 alone does not earn it. The full session stream is written to
    `<workspace>/_agent_logs/` and referenced by `raw_log` — it is a debug artifact, never a
    return value, because handing a multi-megabyte transcript back into graph context evicts
    the very conversation the tool was called to serve.

    Anything Pi wrote to disk is, like all DevChat output, UNTRUSTED generated code until the
    gold-in-gold-out gate.
    """
    workspace = resolve_workspace(_context, "pi_run")

    pi_bin = resolve_binary("pi", "PI_BIN", _PI_BIN_FALLBACKS)
    if pi_bin is None:
        return {
            "ok": False,
            "agent": "pi",
            "error": (
                "pi CLI not found (checked $PI_BIN, PATH, and "
                f"{_PI_BIN_FALLBACKS[0]}). Install with: "
                "npm install -g --ignore-scripts @earendil-works/pi-coding-agent "
                "(see ~/dev/skills/pi/SKILL.md)."
            ),
            "returncode": None,
            "files": {"created": [], "modified": [], "deleted": []},
            "tool_calls": [],
            "final_text": "",
            "workspace": str(workspace),
            "raw_log": None,
        }

    cmd = [
        pi_bin,
        "--provider", provider,
        "--model", model,
        "-p", task,
        "--mode", "json",
        "--no-session",
    ]

    # Rate-budget separation. Both NVIDIA keys carry identical entitlements (verified: same
    # 102-model set), so this split is not about access — it is about not letting the graph's
    # many small choreography calls and Pi's few large build calls contend for one free-tier
    # rate limit. Pi reads `NVIDIA_API_KEY` from the environment (its `--api-key` defaults to
    # env vars), so we hand it key 2 under that name for the child process only; the parent's
    # own environment is untouched and DevChat's agent nodes keep using key 1.
    child_env = None
    if provider == "nvidia":
        secondary = os.environ.get("NVIDIA_API_KEY_2")
        if secondary:
            child_env = {"NVIDIA_API_KEY": secondary}

    return run_agent(
        label="pi",
        cmd=cmd,
        workspace=workspace,
        timeout_seconds=timeout_seconds,
        expect_files=expect_files,
        env=child_env,
    )
