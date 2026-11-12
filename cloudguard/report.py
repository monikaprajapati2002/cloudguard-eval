"""Aggregate results into a markdown report (and a PNG chart if matplotlib is installed)."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import Path


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _rate(rows, key) -> tuple[int, int]:
    rows = [r for r in rows if not r.get("error")]
    return sum(bool(r[key]) for r in rows), len(rows)


def write_report(records: list[dict], out_dir: Path, name: str = "report.md", title: str = "CloudGuard-Eval results") -> Path:
    out_dir = Path(out_dir)
    by_mc = defaultdict(list)
    for r in records:
        by_mc[(r["model"], r["condition"])].append(r)

    lines = [f"# {title}", ""]
    n_err = sum(1 for r in records if r.get("error"))
    lines += [f"Episodes: **{len(records)}** (API errors excluded from rates: {n_err})", ""]

    # --- headline table ------------------------------------------------------
    lines += ["## Headline: unsafe-action rate by model and condition", "",
              "| Model | Condition | N | Unsafe rate (95% CI) | Task success | Asked operator |",
              "|---|---|---|---|---|---|"]
    for (m, c), rows in sorted(by_mc.items()):
        k, n = _rate(rows, "unsafe")
        lo, hi = wilson(k, n)
        s_k, _ = _rate(rows, "task_success")
        a_k, _ = _rate(rows, "asked_user")
        lines.append(f"| {m} | {c} | {n} | {_pct(k / n if n else 0)} ({_pct(lo)}-{_pct(hi)}) | "
                     f"{_pct(s_k / n if n else 0)} | {_pct(a_k / n if n else 0)} |")
    lines.append("")

    # --- per-scenario pivot ----------------------------------------------------
    scenarios = sorted({r["scenario"] for r in records})
    cols = sorted(by_mc)
    lines += ["## Unsafe rate per scenario", "",
              "| Scenario | " + " | ".join(f"{m}<br>{c}" for m, c in cols) + " |",
              "|---|" + "---|" * len(cols)]
    for s in scenarios:
        cells = []
        for mc in cols:
            rows = [r for r in by_mc[mc] if r["scenario"] == s]
            k, n = _rate(rows, "unsafe")
            cells.append(f"{k}/{n}" if n else "-")
        lines.append(f"| {s} | " + " | ".join(cells) + " |")
    lines.append("")

    # --- outcome mix -------------------------------------------------------------
    lines += ["## Outcome mix", "", "| Model | Condition | " + " | ".join(
        ["UNSAFE", "SAFE_COMPLETED", "SAFE_ESCALATED", "INCOMPLETE"]) + " |", "|---|---|---|---|---|---|"]
    for (m, c), rows in sorted(by_mc.items()):
        cnt = Counter(r["outcome"] for r in rows if not r.get("error"))
        lines.append(f"| {m} | {c} | " + " | ".join(str(cnt.get(o, 0)) for o in
                     ["UNSAFE", "SAFE_COMPLETED", "SAFE_ESCALATED", "INCOMPLETE"]) + " |")
    lines.append("")

    # --- injection reporting -------------------------------------------------------
    inj = [r for r in records if "flagged_injection" in r and not r.get("error")]
    if inj:
        lines += ["## Prompt-injection handling (s03)", "",
                  "| Model | Condition | Complied with injection | Flagged it to operator |", "|---|---|---|---|"]
        g = defaultdict(list)
        for r in inj:
            g[(r["model"], r["condition"])].append(r)
        for (m, c), rows in sorted(g.items()):
            n = len(rows)
            lines.append(f"| {m} | {c} | {sum(r['unsafe'] for r in rows)}/{n} | "
                         f"{sum(r['flagged_injection'] for r in rows)}/{n} |")
        lines.append("")

    # --- failure examples ---------------------------------------------------------
    reasons = Counter(reason for r in records for reason in r.get("unsafe_reasons", []))
    if reasons:
        lines += ["## Most common unsafe actions", ""]
        lines += [f"- {reason} - **{k}x**" for reason, k in reasons.most_common(15)]
        lines.append("")
    lines += ["Full transcripts for every episode are in `transcripts/`.", ""]

    # --- chart --------------------------------------------------------------------
    chart = _chart(by_mc, out_dir)
    if chart:
        lines.insert(4, f"![Unsafe rate by condition]({chart.name})\n")

    path = out_dir / name
    path.write_text("\n".join(lines))
    return path


def _chart(by_mc, out_dir: Path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    models = sorted({m for m, _ in by_mc})
    conds = sorted({c for _, c in by_mc})
    width = 0.8 / max(1, len(models))
    fig, ax = plt.subplots(figsize=(1.8 + 2.2 * len(conds), 4))
    for i, m in enumerate(models):
        xs, ys, errs = [], [], [[], []]
        for j, c in enumerate(conds):
            k, n = _rate(by_mc.get((m, c), []), "unsafe")
            p = k / n if n else 0
            lo, hi = wilson(k, n)
            xs.append(j + (i - (len(models) - 1) / 2) * width)
            ys.append(100 * p)
            errs[0].append(100 * (p - lo))
            errs[1].append(100 * (hi - p))
        ax.bar(xs, ys, width, yerr=errs, capsize=3, label=m)
    ax.set_xticks(range(len(conds)))
    ax.set_xticklabels(conds)
    ax.set_ylabel("Unsafe-action rate (%)")
    ax.set_ylim(0, 100)
    ax.set_title("CloudGuard-Eval: unsafe actions by system-prompt condition (95% CI)")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    path = out_dir / "unsafe_rate.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
