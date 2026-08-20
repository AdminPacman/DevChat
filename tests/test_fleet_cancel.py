"""Cancel must actually reach a running fleet subprocess.

THE BUG THIS PINS DOWN. Pressing Cancel set `session.cancel_event`, which is checked
by `_ensure_not_cancelled()` BEFORE a node's tool loop — but never inside
`run_agent`'s blocking `proc.wait()`. Once kimi_run or claude_run had started, Cancel
did nothing until the child exited on its own or hit its timeout: up to 900 seconds of
a button that looked like it worked.

These tests spawn a REAL long-running subprocess and cancel it, because the only
convincing evidence that a kill lands is a kill landing. They deliberately use a
generous sleep so that "it returned quickly" cannot be luck.
"""

import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from functions.function_calling._fleet_common import (  # noqa: E402
    _AGENTS_LOCK,
    _LIVE_AGENTS,
    cancel_agents_under,
    clear_cancel_for,
    run_agent,
)

SLEEP_SECONDS = 60          # far longer than any assertion window
CANCEL_AFTER = 1.5
MUST_RETURN_WITHIN = 15.0   # generous; the bug would take SLEEP_SECONDS


def _run_in_thread(workspace: Path, holder: dict):
    def _target():
        holder["result"] = run_agent(
            label="sleeper",
            cmd=["sleep", str(SLEEP_SECONDS)],
            workspace=workspace,
            timeout_seconds=SLEEP_SECONDS + 120,   # timeout must NOT be what saves us
            expect_files=False,
        )
        holder["returned_at"] = time.monotonic()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread


def test_cancel_kills_a_running_agent_promptly():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        workspace = root / "code_workspace"
        workspace.mkdir()
        clear_cancel_for(root)

        holder: dict = {}
        started = time.monotonic()
        thread = _run_in_thread(workspace, holder)

        time.sleep(CANCEL_AFTER)
        killed = cancel_agents_under(root)
        assert killed == 1, f"expected to find exactly 1 live agent, found {killed}"

        thread.join(timeout=MUST_RETURN_WITHIN)
        assert not thread.is_alive(), (
            f"run_agent did not return within {MUST_RETURN_WITHIN}s of the cancel — "
            "this is the original bug: the wait was blocking and Cancel could not reach it"
        )

        elapsed = holder["returned_at"] - started
        assert elapsed < MUST_RETURN_WITHIN, f"took {elapsed:.1f}s"
        assert elapsed < SLEEP_SECONDS / 2, (
            f"returned in {elapsed:.1f}s — suspiciously close to letting the child finish"
        )

        result = holder["result"]
        assert result["ok"] is False, "a cancelled run is not a success"
        assert "cancelled" in (result.get("error") or "").lower(), (
            f"cancellation must be reported as cancellation, not something else: "
            f"{result.get('error')!r}"
        )
        assert "timed out" not in (result.get("error") or "").lower(), (
            "cancellation must NOT be misreported as a timeout — that misattributes "
            "the decision and invites a pointless retry"
        )


def test_cancel_before_spawn_still_stops_the_agent():
    """A cancel landing in the window between the decision and the spawn must still
    stop the agent, rather than letting one last child run to completion."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        workspace = root / "code_workspace"
        workspace.mkdir()

        cancel_agents_under(root)          # cancel arrives FIRST
        holder: dict = {}
        started = time.monotonic()
        thread = _run_in_thread(workspace, holder)

        thread.join(timeout=MUST_RETURN_WITHIN)
        assert not thread.is_alive(), "an agent spawned after a cancel ran on regardless"
        assert (holder["returned_at"] - started) < MUST_RETURN_WITHIN
        assert "cancelled" in (holder["result"].get("error") or "").lower()

        clear_cancel_for(root)


def test_unrelated_workspace_is_not_cancelled():
    """Cancelling one session must not kill another session's agents."""
    with tempfile.TemporaryDirectory() as tmp:
        root_a = Path(tmp) / "session_a"
        root_b = Path(tmp) / "session_b"
        (root_a / "code_workspace").mkdir(parents=True)
        (root_b / "code_workspace").mkdir(parents=True)
        clear_cancel_for(root_a)
        clear_cancel_for(root_b)

        holder: dict = {}
        thread = _run_in_thread(root_a / "code_workspace", holder)
        time.sleep(CANCEL_AFTER)

        killed = cancel_agents_under(root_b)
        assert killed == 0, f"cancelling session_b hit {killed} of session_a's agents"
        assert thread.is_alive(), "session_a's agent was killed by session_b's cancel"

        cancel_agents_under(root_a)        # clean up
        thread.join(timeout=MUST_RETURN_WITHIN)
        clear_cancel_for(root_a)
        clear_cancel_for(root_b)


def test_no_orphan_process_group_survives():
    """The child runs in its own process group specifically so its descendants die
    with it. Verify the whole group is gone.

    Checks the ACTUAL pid taken from the live registry rather than grepping for a
    command line — a `pgrep -f 'sleep 60'` matches the very shell running it, which
    is exactly the self-match trap that has bitten this rig before.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        workspace = root / "code_workspace"
        workspace.mkdir()
        clear_cancel_for(root)

        holder: dict = {}
        thread = _run_in_thread(workspace, holder)

        # Wait for the child to actually be registered, then take its real pid.
        pid = None
        for _ in range(60):
            with _AGENTS_LOCK:
                live = [p for (p, ws, _l) in _LIVE_AGENTS.values() if ws == workspace]
            if live:
                pid = live[0].pid
                break
            time.sleep(0.1)
        assert pid is not None, "the agent never registered — cannot verify the kill"

        cancel_agents_under(root)
        thread.join(timeout=MUST_RETURN_WITHIN)
        assert not thread.is_alive()

        # Popen.wait() has reaped it, so the pid must no longer be signalable.
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
            raise AssertionError(f"child pid {pid} survived the cancel")
        except ProcessLookupError:
            pass  # gone, as required
        except PermissionError:
            raise AssertionError(f"pid {pid} was recycled to another owner mid-test")


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
                passed += 1
            except AssertionError as exc:
                print(f"  FAIL  {name}: {exc}")
                failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
