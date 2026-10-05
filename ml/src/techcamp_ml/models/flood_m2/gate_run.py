"""The one read of the blocked test block: ADR-0020 step 8 (docs/08 §Reglas de gobierno,
"Compuerta estadística").

`harness.promotion.decide_promotion` is the only reader of the test block in the repository
and this module is its only caller. Everything before the call is train and validation; the
call happens once per candidate and its answer is typed, with the reasons it refused named.
There is no `force_promote` here either, and none in the harness: when the gate refuses,
what production serves is the **best validation baseline** (docs/08 §M2 "Línea base servida",
D-T0.5), and `GateRun.served` says which model version that is. That is not a failure of the
run; it is the answer, and the model card §9 records it.

The order of the steps is a choice worth stating:

* **The register is written before the gate is called.** Its rows hold validation numbers
  only, so they are worth having whether the gate promotes or refuses, and the gate is the
  last irreversible step of the run.
* **The read receipt outlives the process.** `harness.promotion._SPENT_READS` is
  process-level, so a second run of this module in a second interpreter would spend a second
  read of the same rows and answer a question docs/08 §Reglas de gobierno ("Test intocable")
  allows once. `record_spend` writes the receipt to the derived build products, and
  `run_gate` refuses to start when one is there. The receipt only ever blocks: it cannot
  make a gate pass, and it is written *after* the gate answered.

The blocked months enter this module only as the argument `decide_promotion` takes. Nothing
else here reads them, and `harness.split.split` hands out train and validation and nothing
else.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from techcamp_ml.harness.metrics import BOOTSTRAP_RESAMPLES
from techcamp_ml.harness.promotion import PromotionDecision, SpentTestBlocks, decide_promotion
from techcamp_ml.harness.split import DevelopmentSplit, split
from techcamp_ml.models.flood_m2 import anomalies, experiments
from techcamp_ml.models.flood_m2.experiments import BASELINE, CANDIDATE, DISCARDED, Entry, Search
from techcamp_ml.models.flood_m2.robustness import RobustnessReport, robustness
from techcamp_ml.models.flood_m2.thresholds import CalibratedCandidate, OperatingCuts, read_cuts
from techcamp_ml.sources.layout import DEFAULT_LAYOUT, Layout

RECEIPT_NAME = "gate.json"
"""The read receipt of the blocked test block, under the derived build products."""

HYPOTHESES: dict[str, tuple[str, str]] = {
    "climatology_month": (
        "the historical frequency of the calendar month already orders the risk",
        "the frequency of the label by calendar month, fitted on train",
    ),
    "rainfall_6m": (
        "accumulated rainfall of six months alone orders the risk",
        "the flood frequency of five bins of precip_sum_6m, forced monotone",
    ),
    "logistic_regression": (
        "a linear model over the whole feature contract beats both baselines",
        "a logistic regression over FEATURE_NAMES with a train-fitted median",
    ),
    "lightgbm": (
        "the interaction between rainfall and soil moisture beats the linear model",
        "LightGBM over the same contract, tuned on train and validation",
    ),
}
"""One hypothesis and one change per rung (ADR-0020 paso 5). The register carries all four
whatever the run decided, because "aunque no mejore" is the part that makes a register
auditable."""


@dataclass(frozen=True, slots=True)
class GateRun:
    """Everything one run of steps 4-9 produced, in the order it produced it."""

    ladder: tuple[Search, ...]
    candidate: Search
    baseline: Search
    cuts: OperatingCuts
    robustness: RobustnessReport
    decision: PromotionDecision
    register: tuple[Entry, ...]

    @property
    def promote(self) -> bool:
        return self.decision.promote

    @property
    def served(self) -> str:
        """The `model_version` production keeps (docs/08 §M2 "Línea base servida", D-T0.5).

        The candidate when the gate promoted it, the best validation baseline otherwise. The
        baseline is a `model_version` row of its own with `is_baseline`, so every
        `risk_prediction` has a `model_version_id` either way.
        """
        return self.candidate.name if self.decision.promote else self.baseline.name

    def report(self) -> str:
        """The gate report of ADR-0020 step 8 and the robustness report of step 9, as text."""
        report = self.decision.report
        lines = [
            "== ladder (validation only) ==",
            *(
                f"  {rung.name:<20} {rung.kind:<9} PR-AUC {rung.pr_auc:.6f} "
                f"[{rung.interval.lower:.6f}, {rung.interval.upper:.6f}] "
                f"Brier {rung.brier:.6f}"
                for rung in self.ladder
            ),
            "",
            "== operating cuts (validation) ==",
            f"  {self.cuts.report()}",
            "",
            "== gate (blocked test block, one read) ==",
            f"  rows {report.test_rows}, positives {report.test_positives}",
            f"  candidate {self.candidate.name}: PR-AUC {report.candidate.pr_auc:.6f}, "
            f"Brier {report.candidate.brier:.6f}",
            f"  baseline  {self.baseline.name}: PR-AUC {report.baseline.pr_auc:.6f}, "
            f"Brier {report.baseline.brier:.6f}",
            f"  improvement {report.improvement.point:+.6f} "
            f"[{report.improvement.lower:+.6f}, {report.improvement.upper:+.6f}]",
            f"  promote: {self.decision.promote}"
            + (f" (refused: {', '.join(self.decision.reasons)})" if self.decision.reasons else ""),
            f"  served: {self.served}",
            "",
            "== robustness (train and validation only) ==",
            self.robustness.report(),
        ]
        return "\n".join(lines)


def run_gate(
    table: pd.DataFrame,
    development: DevelopmentSplit,
    *,
    reads: SpentTestBlocks,
    register: Path | None = None,
    trials: int = experiments.TRIALS,
    seed: int = experiments.TUNING_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
    today: str | None = None,
) -> GateRun:
    """Steps 4-9 of ADR-0020 over one candidate, with the gate called exactly once.

    `reads` is the run's own receipt object and is required: the harness's process-level
    guard is what refuses a second read *within* a process, and the caller's object is what
    the caller can inspect afterwards.
    """
    ladder = experiments.run_ladder(
        development.train, development.validation, trials=trials, seed=seed, resamples=resamples
    )
    candidate = experiments.select(ladder, experiments.MODEL)
    baseline = experiments.select(ladder, BASELINE)
    calibrated = CalibratedCandidate.fit(candidate.scorer, development.validation)
    cuts = read_cuts(calibrated, development.validation)
    report = robustness(lambda rows: experiments.refit(candidate, rows), development)
    stamp = today or date.today().isoformat()
    rows = tuple(
        _row(rung, candidate, baseline, stamp) for rung in sorted(ladder, key=lambda one: one.name)
    )
    written = _append(register, rows)
    decision = decide_promotion(table, candidate=calibrated, baseline=baseline.scorer, reads=reads)
    return GateRun(
        ladder=ladder,
        candidate=candidate,
        baseline=baseline,
        cuts=cuts,
        robustness=report,
        decision=decision,
        register=written,
    )


def receipt_path(layout: Layout = DEFAULT_LAYOUT) -> Path:
    """Where the read receipt of the blocked test block lives: beside the derived anomaly
    table, out of git."""
    return anomalies.derived_path(layout).parent / RECEIPT_NAME


def already_spent(layout: Layout = DEFAULT_LAYOUT) -> bool:
    return receipt_path(layout).is_file()


def record_spend(
    decision: PromotionDecision,
    candidate: str,
    baseline: str,
    layout: Layout = DEFAULT_LAYOUT,
) -> Path:
    """Write the receipt of this run's single read of the test block.

    Called *after* the gate answered, so a run that crashed before the gate leaves no
    receipt and can be repeated; a run that got an answer cannot be repeated, which is the
    whole of docs/08 §Reglas de gobierno, "Test intocable".
    """
    path = receipt_path(layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "candidate": candidate,
                "baseline": baseline,
                "promote": decision.promote,
                "reasons": list(decision.reasons),
                "test_rows": decision.report.test_rows,
                "test_positives": decision.report.test_positives,
                "read_on": date.today().isoformat(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def guard(layout: Layout = DEFAULT_LAYOUT) -> int:
    """`2` when the receipt of a previous run is there, `0` when it is not.

    A second run of `main` would spend a second read of the same rows, which is iterating
    against the test set wearing a script.
    """
    if not already_spent(layout):
        return 0
    print(
        f"the blocked test block has already been read ({receipt_path(layout)}); a second run "
        "would iterate against the test set, and the report of that run is the one already "
        "written.",
        file=sys.stderr,
    )
    return 2


def main(argv: Sequence[str] | None = None, *, layout: Layout = DEFAULT_LAYOUT) -> int:
    """Run steps 4-9 once and print the report."""
    parser = argparse.ArgumentParser(description="M2 experiment cycle and one gate read")
    parser.add_argument("--trials", type=int, default=experiments.TRIALS)
    parser.add_argument("--seed", type=int, default=experiments.TUNING_SEED)
    parser.add_argument("--today", default=None, help="the date the register rows carry")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the ladder and read the cuts without calling the gate or writing a receipt",
    )
    args = parser.parse_args(argv)
    if guard(layout):
        return 2
    table = anomalies.load_features(layout)
    development = split(table)
    if args.dry_run:
        ladder = experiments.run_ladder(
            development.train, development.validation, trials=args.trials, seed=args.seed
        )
        candidate = experiments.select(ladder, experiments.MODEL)
        baseline = experiments.select(ladder, BASELINE)
        print(
            "\n".join(
                f"  {rung.name:<20} {rung.kind:<9} PR-AUC {rung.pr_auc:.6f} "
                f"[{rung.interval.lower:.6f}, {rung.interval.upper:.6f}] "
                f"Brier {rung.brier:.6f}"
                for rung in ladder
            )
        )
        calibrated = CalibratedCandidate.fit(candidate.scorer, development.validation)
        print(read_cuts(calibrated, development.validation).report())
        print(f"  candidate {candidate.name}, baseline {baseline.name}")
        return 0
    run = run_gate(
        table,
        development,
        reads=SpentTestBlocks(),
        trials=args.trials,
        seed=args.seed,
        today=args.today,
    )
    record_spend(run.decision, run.candidate.name, run.baseline.name)
    print(run.report())
    return 0


def _row(rung: Search, candidate: Search, baseline: Search, stamp: str) -> Entry:
    hypothesis, change = HYPOTHESES[rung.name]
    if rung.name == candidate.name:
        decision = CANDIDATE
    elif rung.name == baseline.name:
        decision = BASELINE
    else:
        decision = DISCARDED
    return experiments.entry_for(
        rung, date=stamp, hypothesis=hypothesis, change=change, decision=decision
    )


def _append(register: Path | None, rows: tuple[Entry, ...]) -> tuple[Entry, ...]:
    if register is None:
        return rows
    return tuple(experiments.append_register(register, row) for row in rows)


if __name__ == "__main__":
    sys.exit(main())
