"""Re-score a finished run with the current grader -- no API calls, no cost.

Each saved transcript holds the exact tool calls the agent made. We replay those
calls on a fresh simulated account, rebuild what the agent said, and grade again.

Usage:
  python -m cloudguard.regrade results/20261112-050614

Writes results_regraded.jsonl and report_regraded.md next to the originals
(the originals are left untouched) and prints which episodes changed outcome.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from .agents import collect_agent_text
from .env import CloudEnv
from .grader import grade
from .report import write_report
from .scenarios import SCENARIO_MAP


def regrade_file(path: Path) -> tuple[dict, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    old = data["record"]
    scenario = SCENARIO_MAP[old["scenario"]]

    env = CloudEnv(scenario.state)
    for entry in data.get("action_log", []):  # replay the agent's actions exactly
        env.call(entry["tool"], entry.get("args", {}))
    if old.get("summary") and not env.summary:
        env.summary = old["summary"]
    if old.get("stop_reason") in ("finish", "end_turn"):
        env.finished = True
    env.agent_text = collect_agent_text(data.get("transcript", []), env.summary)

    new = {**old, **grade(scenario, env)}
    if old.get("error"):  # API failures stay failures
        new["outcome"] = "INCOMPLETE"
    return old, new


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print(__doc__)
        return 2
    run_dir = Path(argv[0])
    files = sorted((run_dir / "transcripts").glob("*.json"))
    if not files:
        print(f"No transcripts found in {run_dir / 'transcripts'}")
        return 1

    records, changes = [], Counter()
    for f in files:
        old, new = regrade_file(f)
        records.append(new)
        if old["outcome"] != new["outcome"]:
            changes[(old["scenario"], old["model"], old["outcome"], new["outcome"])] += 1

    records.sort(key=lambda r: (r["model"], r["condition"], r["scenario"], r["trial"]))
    with open(run_dir / "results_regraded.jsonl", "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, default=str) + "\n")
    report = write_report(records, run_dir, name="report_regraded.md",
                          title="CloudGuard-Eval results (re-graded with grader v2)")

    print(f"Re-graded {len(records)} episodes.")
    if changes:
        print("Outcome changes:")
        for (scen, model, a, b), n in sorted(changes.items()):
            print(f"  {scen:<32} {model:<18} {a} -> {b}  x{n}")
    else:
        print("No outcomes changed.")
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
