"""Step 10 of ADR-0020: the `model_version` row and the artifact in object storage.

This module answers one question — **which version does production serve** — and writes
down what that answer is, in the two places that make it checkable later: the artifact in
the `ml` bucket (`models/risk_flood/<version>/climatology.json`, D-T9.4) and the row of
docs/03-modelo-datos.md §`model_version`. Everything the job reads at 06:00 comes from
there: which bytes to load, whether those bytes are the ones that were registered
(`artifact_sha256`), and which operating points apply (`thresholds`).

Four rules shape the code, and each is a refusal rather than a convenience:

* **The gate is read, never re-run.** `serve_name` opens `derived/gate.json`
  (`gate_run.receipt_path`, the receipt of the single read of the blocked test block) and
  answers the same way `GateRun.served` does: the candidate when the gate promoted it,
  the best validation baseline otherwise (docs/08 §M2 "Línea base servida", D-T0.5). The
  receipt is read and left alone — a module that rewrote it would erase the evidence that
  the block was spent — and without one there is no answer to record, so this refuses
  rather than registering a guess.
* **The artifact is fitted on train and only on train.** `export_artifact` is handed
  `split(...).train`: `ClimatologyBaseline.fit` never sees validation and the test block is
  not even reachable from this module (`harness.split.split` hands out train and validation
  and nothing else). The bytes are `json.dumps(..., sort_keys=True, indent=2)` plus a
  newline, so the same train gives the same artifact on any machine and the digest in the
  row is a promise about reproducible bytes (docs/08 §Reglas de gobierno, "Reproducible o
  no existe").
* **The digest travels with the bytes.** `artifact_sha256` is the sha256 of exactly the
  body that was uploaded, and the reader refuses to deserialize an object whose digest does
  not match (docs/03 §Integridad del artefacto: "un objeto cambiado en el bucket no se
  ejecuta"). The row therefore carries the version, the dataset hash, the commit and the
  four register figures, which is docs/08 §Reglas de gobierno "Trazabilidad" in full.
* **Nothing is written twice.** A `version` already in `model_version` is returned as it
  is, because artifacts are never deleted (docs/08 §Reglas de gobierno, "Reversión") and a
  second row for the same version would be a second claim about the same bytes.

`thresholds` is `{}` for this baseline (D-T9.2): the model card's cuts belong to the
calibrated LightGBM candidate, and `severity_for` reads a missing threshold as missing
evidence, so every prediction of this version stays `low` instead of carrying an operating
point that was never calibrated for a climatology. Only `risk_flood` is registered in this
lane (D-T9.3).

**This module never writes the receipt, never calls the gate and never reads the test
block.** The order of a real run is `gate_run.main` first and this CLI after it.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]
import pandas as pd
from botocore.config import Config  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession
from techcamp.risk.adapters.repositories import SqlAlchemyRiskRepository
from techcamp.risk.application.ports import RiskRepository
from techcamp.risk.domain.models import EventType, ModelVersion
from techcamp.shared.config import (
    s3_access_key,
    s3_ml_bucket,
    s3_public_url,
    s3_region,
    s3_secret_key,
)
from techcamp.shared.db import async_session_factory
from techcamp.shared.ids import uuid7

from techcamp_ml.datasets.flood_m2 import DATASET_NAME, MANIFEST_NAME
from techcamp_ml.harness.split import split, train_climatology_years
from techcamp_ml.models.flood_m2 import anomalies, gate_run
from techcamp_ml.models.flood_m2.baselines import ClimatologyBaseline
from techcamp_ml.models.flood_m2.experiments import LOG_COLUMNS, LOG_PATH
from techcamp_ml.sources.layout import DEFAULT_LAYOUT, Layout

ARTIFACT_KIND = "climatology"
"""What the artifact in the bucket *is*, the first key the reader checks: a climatology of
the served rung, not a fitted estimator and not a calibration."""

ARTIFACT_FILENAME = "climatology.json"
ARTIFACT_CONTENT_TYPE = "application/json"
"""The artifact is JSON and nothing else, so its content type is stated here instead of
letting boto3 guess one from a file name."""

MODEL_PREFIX = "models/risk_flood"
"""docs/08 §Estructura de `ml/`: `models/<nombre>/<versión>/` under the `ml` bucket
(ADR-0018, D-T9.4)."""

METRIC_COLUMNS: Mapping[str, str] = {
    "pr_auc": "val_pr_auc",
    "pr_auc_ci_low": "val_pr_auc_ci_low",
    "pr_auc_ci_high": "val_pr_auc_ci_high",
    "brier": "val_brier",
}
"""`baseline_metrics`' own four keys over the register's four validation columns. docs/04
§Riesgo answers a served baseline with `baseline_metrics`, and the register of ADR-0020
step 5 is where those figures were written — nothing is estimated twice."""


def serve_name(layout: Layout = DEFAULT_LAYOUT) -> str:
    """The rung production serves, read from the gate's receipt.

    The same answer `GateRun.served` gives, from the file `record_spend` wrote after the
    gate decided: the candidate when it promoted, the best validation baseline otherwise.
    The receipt is opened read-only and never rewritten, and its absence is a refusal —
    registration before the gate ran would record a version nothing ever compared.

    A promoted candidate is refused rather than exported. This module fits one specific
    model, `ClimatologyBaseline`, and writes its bytes under `"kind": "climatology"`; a
    gate that promoted `lightgbm` would otherwise produce a row named
    `...-lightgbm` whose artifact is a climatology and whose `baseline_metrics` are the
    climatology's, which is a `model_version` that lies about what serves production. The
    ladder's promoted rung needs its own exporter, which is a different work unit.
    """
    receipt = _read_receipt(layout)
    promoted = receipt.get("promote")
    if not isinstance(promoted, bool):
        raise ValueError(
            f"the gate receipt at {gate_run.receipt_path(layout)} holds no boolean "
            f"'promote' answer, so which version production serves is unknown"
        )
    if promoted:
        raise ValueError(
            f"the gate receipt at {gate_run.receipt_path(layout)} promoted "
            f"{receipt.get('candidate')!r}, and this exporter only fits the climatology "
            f"baseline: a promoted candidate is a different model and needs its own "
            f"artifact, which registering the baseline's bytes under its name would "
            f"misreport"
        )
    rung = receipt.get("baseline")
    if not isinstance(rung, str) or not rung:
        raise ValueError(
            f"the gate receipt at {gate_run.receipt_path(layout)} names no baseline rung"
        )
    return rung


def gate_run_on(layout: Layout = DEFAULT_LAYOUT) -> str:
    """The day the gate answered, from the receipt's own `read_on`.

    The default for the `version` string's date, and the day the run happened rather than
    the day this module is executed: a registration run a week later is still the version
    of the run that decided, which is what makes the version string mean one thing.
    """
    read_on = _read_receipt(layout).get("read_on")
    if not isinstance(read_on, str) or not read_on:
        raise ValueError(
            f"the gate receipt at {gate_run.receipt_path(layout)} records no 'read_on' day, "
            "so the version it registered cannot be dated"
        )
    return read_on


def version_for(rung: str, today: str) -> str:
    """`<YYYY-MM-DD de la corrida del gate>-<rung>`: the `model_version.version` string and
    the artifact's directory, identical in both (docs/08 §Estructura de `ml/`)."""
    return f"{today}-{rung}"


def artifact_key(version: str) -> str:
    """`models/risk_flood/<version>/climatology.json`, the object key in the bucket."""
    return f"{MODEL_PREFIX}/{version}/{ARTIFACT_FILENAME}"


def artifact_uri(bucket: str, version: str) -> str:
    """The `s3://` URI the row carries, so the reader never rebuilds a key by hand."""
    return f"s3://{bucket}/{artifact_key(version)}"


def export_artifact(version: str, train: pd.DataFrame) -> bytes:
    """The served rung's climatology as the exact bytes `artifact_sha256` digests.

    `train` is `split(...).train` and nothing else: `ClimatologyBaseline.fit` reads the
    label's frequency by calendar month out of it, validation keeps its real frequency and
    the blocked test block is not reachable from this module at all (docs/08 §Reglas de
    gobierno, "Frecuencia real"; ADR-0020: el agente no puede tocar el test).

    `version` is the very string the row's `version` holds, and the artifact repeats it:
    the reader compares the two, so a document whose `version` disagrees with its own row
    is a document about a different model.

    `train_years` is `split.train_climatology_years()` — the years the anomalies were
    derived against — because D-T6b.1 left the served job without a climatology and this
    is where the years of a served version are answered (D-T9.4).

    Serialized with sorted keys and two-space indent plus a trailing newline: the digest in
    the row is only reproducible while the bytes are.
    """
    baseline = ClimatologyBaseline.fit(train)
    document: dict[str, Any] = {
        "kind": ARTIFACT_KIND,
        "version": version,
        "train_years": list(train_climatology_years()),
        "prevalence": baseline.prevalence,
        "by_month": {
            str(month): frequency for month, frequency in sorted(baseline.by_month.items())
        },
        "fitted_rows": int(len(train)),
        "fitted_positives": int(train["label"].sum()),
    }
    return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")


def artifact_sha256(payload: bytes) -> str:
    """The lowercase sha256 of exactly these bytes, as `model_version.artifact_sha256`
    holds it (docs/03 §Integridad del artefacto)."""
    return hashlib.sha256(payload).hexdigest()


def baseline_metrics(rung: str, *, path: Path = LOG_PATH) -> dict[str, float]:
    """The four validation figures the register holds for `rung`.

    The **last** row for the rung, because a rung can be run more than once and the row
    that describes the version being served is the newest one. Read-only: this module never
    appends to the register, and a header that is not `experiments.LOG_COLUMNS` is refused
    rather than read into keys of a different shape (`append_register` refuses the same
    header for the same reason).
    """
    if not path.is_file():
        raise ValueError(
            f"the experiment register {path} is not there, so the served rung {rung!r} has no "
            "validation figures to register"
        )
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != LOG_COLUMNS:
            raise ValueError(
                f"{path} opens with {reader.fieldnames} and the register is read with "
                f"{list(LOG_COLUMNS)}"
            )
        rows = [row for row in reader if row.get("model") == rung]
    if not rows:
        raise ValueError(
            f"the register {path} holds no row for the served rung {rung!r}; every rung the "
            "run kept is registered, so a missing one means the register and the gate "
            "disagree about what ran"
        )
    row = rows[-1]
    return {name: float(row[column] or "") for name, column in METRIC_COLUMNS.items()}


def dataset_hash(layout: Layout = DEFAULT_LAYOUT) -> str:
    """The `sha256` the dataset's manifest records, read-only.

    It is the digest of `flood_m2.parquet` as T4 built it, so a `model_version` row names
    the dataset the artifact was fitted on and not one that happens to be on disk now
    (docs/08 §Reglas de gobierno, "Trazabilidad")."""
    path = layout.ml_root / "datasets" / DATASET_NAME / MANIFEST_NAME
    if not path.is_file():
        raise ValueError(
            f"the dataset manifest {path} is not there, so the artifact's dataset cannot be "
            "traced to a hash"
        )
    recorded = json.loads(path.read_text(encoding="utf-8")).get("sha256")
    if not isinstance(recorded, str) or not recorded:
        raise ValueError(f"the dataset manifest {path} records no 'sha256' to register")
    return recorded


def git_commit(layout: Layout = DEFAULT_LAYOUT) -> str:
    """`git rev-parse HEAD` of the worktree that produced the artifact.

    The other half of "reproducible o no existe": with the dataset hash it says which
    code and which data made these bytes, which is what a reader needs to rebuild them.
    """
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "HEAD"],
            cwd=layout.ml_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(
            f"git could not name the commit of {layout.ml_root} ({error}), so the artifact "
            "would be registered without the code that produced it"
        ) from error
    return completed.stdout.strip()


def s3_client() -> Any:
    """The S3 client registration uploads with, against the public endpoint.

    Path style and SigV4, the same client shape as
    `logbook/adapters/attachments.Boto3Presigner` (ADR-0018): the seminar profile stores
    artifacts in MinIO through the same public URL the developer's host reaches
    (D-T9.5), and a virtual-hosted URL would hand the emulator a host that does not
    resolve.
    """
    return boto3.client(
        "s3",
        endpoint_url=s3_public_url(),
        region_name=s3_region(),
        aws_access_key_id=s3_access_key(),
        aws_secret_access_key=s3_secret_key(),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def risk_repository(session: AsyncSession) -> RiskRepository:
    """The real `RiskRepository` over `session` — the port of `risk/application/ports.py`
    and its SQLAlchemy adapter, so registration writes through the same mapper
    `served_version` reads."""
    return SqlAlchemyRiskRepository(session)


async def register(
    session: AsyncSession,
    *,
    layout: Layout = DEFAULT_LAYOUT,
    bucket: str | None = None,
    today: str | None = None,
    repository: RiskRepository | None = None,
    client: Any | None = None,
) -> ModelVersion:
    """Register the version the gate left serving: artifact first, row second.

    The artifact is in the bucket before the row exists, because a `risk_prediction` names
    a `model_version_id` and has to be able to read the bytes it points at
    (`RiskRepository.insert_version`). The row then carries what makes the registration
    auditable: the version string, the `s3://` URI, the digest of exactly the uploaded
    bytes, the dataset hash and the commit (docs/08 §Reglas de gobierno, "Trazabilidad"),
    the four register figures as `baseline_metrics`, and — D-T9.1, D-T9.2 —
    `is_baseline=True`, `promoted=False` and `thresholds={}`.

    A `version` that `versions_for` already holds returns that row and writes nothing:
    the artifacts are never deleted, so a second row would be a second claim about the
    same bytes. The claim is taken under `lock_version`, which is what makes that
    idempotence real rather than merely documented: the read and the write have to see
    each other, or two registrations of one version both observe no row, both upload to
    the same key — and the second upload leaves the object holding bytes the committed
    row's `artifact_sha256` does not describe, which is a version every later prediction
    refuses as unverified.

    `repository` and `client` are seams for the tests, which bring a double instead of a
    database and a bucket (ADR-0002: external I/O needs a test double).
    """
    name = EventType.FLOOD.version_name
    rung = serve_name(layout)
    version = version_for(rung, today or gate_run_on(layout))
    store = repository or risk_repository(session)

    await store.lock_version(name, version)
    existing = next((row for row in await store.versions_for(name) if row.version == version), None)
    if existing is not None:
        return existing

    train = split(anomalies.load_features(layout)).train
    payload = export_artifact(version, train)
    digest = artifact_sha256(payload)
    target = bucket or s3_ml_bucket()
    (client or s3_client()).put_object(
        Bucket=target,
        Key=artifact_key(version),
        Body=payload,
        ContentType=ARTIFACT_CONTENT_TYPE,
    )
    return await store.insert_version(
        ModelVersion(
            id=uuid7(),
            name=name,
            version=version,
            artifact_uri=artifact_uri(target, version),
            is_baseline=True,
            thresholds={},
            promoted=False,
            created_at=datetime.now(UTC),
            metrics=None,
            baseline_metrics=baseline_metrics(rung),
            artifact_sha256=digest,
            dataset_hash=dataset_hash(layout),
            git_commit=git_commit(layout),
        )
    )


def main(argv: Sequence[str] | None = None, *, layout: Layout = DEFAULT_LAYOUT) -> int:
    """`python -m techcamp_ml.models.flood_m2.registration`, run after the gate's own CLI.

    `--dry-run` prints the version, the key and the digest of the bytes a real run would
    upload, and writes nothing: no object, no row, no receipt.
    """
    parser = argparse.ArgumentParser(
        description="Register the served M2 version in the ml bucket and model_version"
    )
    parser.add_argument("--bucket", default=None, help="the ml bucket (config s3_ml_bucket)")
    parser.add_argument("--date", default=None, help="the version's day, default the gate's")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan without uploading or inserting"
    )
    args = parser.parse_args(argv)

    try:
        rung = serve_name(layout)
        version = version_for(rung, args.date or gate_run_on(layout))
        train = split(anomalies.load_features(layout)).train
        payload = export_artifact(version, train)
        digest = artifact_sha256(payload)
    except (OSError, ValueError) as error:
        print(f"nothing was registered: {error}", file=sys.stderr)
        return 1

    target = args.bucket or s3_ml_bucket()
    if args.dry_run:
        print(
            f"served {rung} -> {version}\n"
            f"  s3://{target}/{artifact_key(version)} ({len(payload)} bytes, sha256 {digest})\n"
            f"  thresholds {{}} · is_baseline · not promoted"
        )
        return 0

    async def run() -> ModelVersion:
        async with async_session_factory() as session:
            return await register(session, layout=layout, bucket=target, today=args.date)

    try:
        written = asyncio.run(run())
    except (OSError, ValueError) as error:
        print(f"nothing was registered: {error}", file=sys.stderr)
        return 1
    print(f"registered {written.name}@{written.version} -> {written.artifact_uri}")
    print(f"  artifact_sha256 {written.artifact_sha256}")
    return 0


def _read_receipt(layout: Layout) -> dict[str, Any]:
    """`derived/gate.json`, read once and never written. Its absence means the gate never
    ran (ADR-0020 paso 8), and there is nothing to register until it does."""
    path = gate_run.receipt_path(layout)
    if not path.is_file():
        raise ValueError(
            f"there is no gate receipt at {path}, so the gate has never run and no version "
            "has been decided to serve; run `python -m techcamp_ml.models.flood_m2.gate_run` "
            "first"
        )
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise ValueError(f"the gate receipt at {path} is not an object")
    return receipt


if __name__ == "__main__":
    sys.exit(main())
