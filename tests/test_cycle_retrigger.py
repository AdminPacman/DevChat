"""Regression test: a cycle must not abandon a retry because work also left it.

THE BUG THIS PINS DOWN (seen twice in production crew runs, silently):

`_execute_cycle_with_iterations` used to return the moment ANY external node was
triggered, *before* it ever checked whether the cycle's entry node had been
retriggered. Those are two independent questions, and one conditional edge can
answer both at once.

In the crew's repair graph a `VERDICT: NEEDS_WORK` fires two of Gate's edges in the
same step:

    Gate -> Builder                (the retry — INTERNAL to the cycle)
    Gate -> Repair_Loop_Counter    (EXTERNAL to the cycle)

The old code saw the external one and bailed. And because a cycle super-node is
scheduled exactly once for the entire run, Builder was never revisited: the retry
flag sat on the edge untouched, the run ended with no exception and no retry, and
the human approval node downstream was skipped because none of its predecessors
ever fired.

These tests drive the method directly with stubbed collaborators, so they measure the
control flow rather than a whole graph execution.
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow.executor.cycle_executor import CycleExecutor


class _SilentLog:
    def debug(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass


def _stub(scope_results, retrigger_results):
    """Build a stand-in exposing only what the method under test touches.

    scope_results / retrigger_results are consumed one entry per iteration, so a
    test reads as a script of what each pass through the loop returns.
    """
    calls = {"iterations": 0}

    def _execute_scope_layers(*_a, **_k):
        idx = calls["iterations"]
        calls["iterations"] += 1
        return scope_results[idx] if idx < len(scope_results) else set()

    def _is_initial_node_retriggered(*_a, **_k):
        idx = calls["iterations"] - 1
        return retrigger_results[idx] if idx < len(retrigger_results) else False

    return types.SimpleNamespace(
        log_manager=_SilentLog(),
        _detect_cycles_in_scope=lambda *a, **k: [],
        _build_topological_layers_in_scope=lambda *a, **k: [],
        _execute_scope_layers=_execute_scope_layers,
        _is_initial_node_retriggered=_is_initial_node_retriggered,
        _calls=calls,
    )


def _run(stub, max_iterations=2):
    return CycleExecutor._execute_cycle_with_iterations(
        stub,
        cycle_id="repair",
        cycle_nodes=["Builder", "Patch", "Gate"],
        initial_node_id="Builder",
        max_iterations=max_iterations,
    )


def test_retrigger_wins_over_external_exit():
    """THE REGRESSION. External node triggered AND entry retriggered in the same
    pass must keep looping, not bail. Before the fix this returned after one
    iteration and the retry never happened."""
    stub = _stub(
        scope_results=[{"Repair_Loop_Counter"}, set()],
        retrigger_results=[True, False],
    )
    result = _run(stub)

    assert stub._calls["iterations"] == 2, (
        f"expected the cycle to iterate twice (the retry), got "
        f"{stub._calls['iterations']} — this is the original bug"
    )
    assert result == {"Repair_Loop_Counter"}, (
        f"external targets must still be reported on exit, got {result}"
    )


def test_external_without_retrigger_still_exits():
    """A genuine exit — work left the cycle and nothing came back — must still
    return promptly. The fix must not turn every exit into a full iteration run."""
    stub = _stub(scope_results=[{"Pac_Approval"}], retrigger_results=[False])
    result = _run(stub)

    assert stub._calls["iterations"] == 1
    assert result == {"Pac_Approval"}


def test_plain_completion_returns_empty():
    """No external work, no retrigger: the ordinary finish."""
    stub = _stub(scope_results=[set()], retrigger_results=[False])
    result = _run(stub)

    assert stub._calls["iterations"] == 1
    assert result == set()


def test_external_targets_accumulate_across_iterations():
    """Different external nodes fired on different passes must all survive to the
    return value — accumulating is what makes looping safe."""
    stub = _stub(
        scope_results=[{"A"}, {"B"}, set()],
        retrigger_results=[True, True, False],
    )
    result = _run(stub, max_iterations=5)

    assert result == {"A", "B"}, f"expected both external targets, got {result}"


def test_max_iterations_still_bounds_a_runaway_loop():
    """An entry node that retriggers forever must stop at the cap, and still report
    what it triggered on the way."""
    stub = _stub(
        scope_results=[{"X"}] * 10,
        retrigger_results=[True] * 10,
    )
    result = _run(stub, max_iterations=3)

    assert stub._calls["iterations"] == 3, "the cap must still bound the loop"
    assert result == {"X"}


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
