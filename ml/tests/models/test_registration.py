"""Registration of the served line into `model_version` (ADR-0020 step 10, docs/08 §M2
"Línea base servida").

No database and no MinIO: the repository and the S3 client are doubles and the session is
never touched, because what step 10 has to be right about is the *decision* it records —
which rung the gate left serving, which bytes go to the bucket, which digest travels in the
row — and none of that is the database's property (D-T9.1–D-T9.6).

The register row is read from the real `ml/experiments/log.csv`, because the four figures
`baseline_metrics` carries are the ones the gate run of T8 actually wrote, and a fixture
would pin a number the artifact does not claim.
"""

from __future__ import annotations

import asyncio
import csv
import datetime
import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from techcamp.risk.domain.features import seasonality
from techcamp.risk.domain.models import ModelVersion
from techcamp.shared.config import s3_internal_url, s3_public_url

from techcamp_ml.datasets.flood_m2 import DATASET_NAME, MANIFEST_NAME
from techcamp_ml.models.flood_m2 import anomalies, gate_run, registration
from techcamp_ml.models.flood_m2.experiments import LOG_COLUMNS, LOG_PATH
from techcamp_ml.sources.layout import Layout

BASELINE_RUNG = "climatology_month"
CANDIDATE_RUNG = "lightgbm"
DATE = "2026-10-05"
VERSION = f"{DATE}-{BASELINE_RUNG}"
KEY = f"models/risk_flood/{VERSION}/climatology.json"
DATASET_SHA256 = "7916e97fb4ea7bdc50b4d4cb1bb244683c5dcbf1bf32421ea8c32788983d0be5"
GIT_COMMIT = "0" * 40
UNUSED_SESSION = object()
"""The session is never read: the repository is a double, and a registration that needed a
live connection would not be one this file could check."""

FLOODED_MONTHS = (3, 9)
MUNICIPALITIES = ("08001", "08002")
MONTHS = tuple(range(1, 13))
TRAIN_ROWS = len(MUNICIPALITIES) * len(MONTHS)
TRAIN_POSITIVES = len(FLOODED_MONTHS) * len(MUNICIPALITIES)


class RecordingRepository:
    """The `RiskRepository` port's methods step 10 calls, and nothing else."""

    def __init__(self, existing: Sequence[ModelVersion] = ()) -> None:
        self.existing = list(existing)
        self.inserted: list[ModelVersion] = []
        self.locks: list[tuple[str, str]] = []

    async def versions_for(self, name: str) -> Sequence[ModelVersion]:
        return tuple(self.existing)

    async def lock_version(self, name: str, version: str) -> None:
        self.locks.append((name, version))

    async def insert_version(self, version: ModelVersion) -> ModelVersion:
        self.inserted.append(version)
        return version


class RecordingS3:
    """The one `put_object` call registration makes, with the arguments it made it with."""

    def __init__(self) -> None:
        self.objects: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> None:
        self.objects.append(kwargs)


def train_frame() -> pd.DataFrame:
    """Two municipalities over 2019 with two flooded months in both of them: a train block
    whose prevalence and `by_month` are known by hand (docs/08 §M2 "Unidad")."""
    rows: list[dict[str, object]] = []
    for code in MUNICIPALITIES:
        for month in MONTHS:
            sin, cos = seasonality(month)
            rows.append(
                {
                    "code": code,
                    "horizon_start": pd.Timestamp(f"2019-{month:02d}-01"),
                    "month_sin": sin,
                    "month_cos": cos,
                    "precip_anomaly_1m": 0.0,
                    "precip_anomaly_3m": 0.0,
                    "precip_anomaly_6m": 0.0,
                    "label": int(month in FLOODED_MONTHS),
                }
            )
    return pd.DataFrame(rows)


def _receipt(layout: Layout, *, promote: bool) -> Path:
    """The receipt `gate_run.record_spend` writes, read here and never rewritten."""
    path = gate_run.receipt_path(layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "candidate": CANDIDATE_RUNG,
                "baseline": BASELINE_RUNG,
                "promote": promote,
                "reasons": [] if promote else ["brier_worse_than_the_best_baseline"],
                "test_rows": 48,
                "test_positives": 16,
                "read_on": DATE,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _layout(tmp_path: Path, table: pd.DataFrame, *, promote: bool | None) -> Layout:
    """A layout whose dataset, derived anomalies, manifest and receipt are the fixture, so
    nothing here reads the real cache, the real dataset or the real receipt."""
    layout = Layout(tmp_path)
    anomalies.dataset_path(layout).parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(anomalies.dataset_path(layout), index=False)
    derived = anomalies.derived_path(layout)
    derived.parent.mkdir(parents=True, exist_ok=True)
    table[["code", "horizon_start", *anomalies.ANOMALY_COLUMNS]].to_parquet(derived, index=False)
    manifest = layout.ml_root / "datasets" / DATASET_NAME / MANIFEST_NAME
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"dataset": DATASET_NAME, "file": "flood_m2.parquet", "sha256": DATASET_SHA256})
        + "\n",
        encoding="utf-8",
    )
    if promote is not None:
        _receipt(layout, promote=promote)
    return layout


def _register(layout: Layout, repository: RecordingRepository, client: RecordingS3) -> ModelVersion:
    return asyncio.run(
        registration.register(
            UNUSED_SESSION,  # type: ignore[arg-type]
            layout=layout,
            bucket="ml-artifacts",
            today=DATE,
            repository=repository,
            client=client,
        )
    )


def _registered_version() -> ModelVersion:
    return ModelVersion(
        id=uuid.uuid4(),
        name="risk_flood",
        version=VERSION,
        artifact_uri=f"s3://ml-artifacts/{KEY}",
        is_baseline=True,
        thresholds={},
        promoted=False,
        created_at=datetime.datetime(2026, 10, 5, tzinfo=datetime.UTC),
    )


@pytest.fixture(autouse=True)
def _no_real_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """`git rev-parse HEAD` is the parent's commit to read; here it is a fixed string, so the
    row's `git_commit` is asserted instead of whatever the worktree is at."""
    monkeypatch.setattr(registration, "git_commit", lambda _layout: GIT_COMMIT)


def test_the_exported_artifact_carries_the_prevalence_and_by_month_of_train() -> None:
    payload = json.loads(registration.export_artifact(VERSION, train_frame()))

    assert payload["kind"] == "climatology"
    assert payload["version"] == VERSION
    assert payload["prevalence"] == pytest.approx(TRAIN_POSITIVES / TRAIN_ROWS)
    assert payload["by_month"] == {
        str(month): (1.0 if month in FLOODED_MONTHS else 0.0) for month in MONTHS
    }
    assert payload["train_years"] == [2019, 2020, 2021, 2022]
    assert payload["fitted_rows"] == TRAIN_ROWS
    assert payload["fitted_positives"] == TRAIN_POSITIVES


def test_the_export_is_byte_identical_across_calls() -> None:
    """`artifact_sha256` is only a promise if the same train gives the same bytes
    (docs/08 §Reglas de gobierno, "Reproducible o no existe")."""
    train = train_frame()

    first = registration.export_artifact(VERSION, train)
    second = registration.export_artifact(VERSION, train)

    assert first == second
    assert first.endswith(b"\n")


def test_a_train_with_no_rows_is_refused() -> None:
    """`ClimatologyBaseline.fit` refuses an empty block: a climatology of nothing would
    claim a prevalence nobody observed."""
    with pytest.raises(ValueError, match="at least one train row"):
        registration.export_artifact(VERSION, train_frame().iloc[:0])


@pytest.mark.parametrize("missing", ["month_sin", "month_cos"])
def test_a_train_without_the_seasonality_is_refused(missing: str) -> None:
    """`calendar_month` reads the calendar month off the shared contract's
    `month_sin`/`month_cos`, so a frame without them is not a smaller climatology."""
    train = train_frame().drop(columns=[missing])

    with pytest.raises(ValueError, match=missing):
        registration.export_artifact(VERSION, train)


def test_the_sha256_is_the_hash_of_the_exported_bytes() -> None:
    payload = registration.export_artifact(VERSION, train_frame())

    assert registration.artifact_sha256(payload) == hashlib.sha256(payload).hexdigest()
    assert len(registration.artifact_sha256(payload)) == 64


def test_the_sha256_changes_when_a_single_byte_changes() -> None:
    payload = registration.export_artifact(VERSION, train_frame())
    tampered = payload.replace(b'"prevalence"', b'"prevalencX"')

    assert tampered != payload
    assert registration.artifact_sha256(tampered) != registration.artifact_sha256(payload)


def test_registration_without_a_receipt_is_refused(tmp_path: Path) -> None:
    """The gate never ran: there is no answer to register, and this module never writes a
    receipt of its own (ADR-0020 paso 8 spends the single read of the test block)."""
    layout = _layout(tmp_path, train_frame(), promote=None)

    with pytest.raises(ValueError, match="gate"):
        registration.serve_name(layout)


def test_a_gate_that_refused_serves_the_baseline(tmp_path: Path) -> None:
    """docs/08 §M2 "Línea base servida", D-T0.5: `GateRun.served` answers the baseline when
    nothing was promoted, so that is the rung `model_version` registers."""
    layout = _layout(tmp_path, train_frame(), promote=False)

    assert registration.serve_name(layout) == BASELINE_RUNG


def test_a_gate_that_promoted_is_refused_rather_than_exported_as_a_climatology(
    tmp_path: Path,
) -> None:
    """A promoted rung is a different model, and this exporter only fits the climatology.

    Registering `ClimatologyBaseline`'s bytes under the candidate's name would give a
    `model_version` whose artifact, `baseline_metrics` and `sha256` all describe the
    climatology while its name claims to serve `lightgbm`: a row that misreports what
    production is answering with. Refusing is the honest answer, and the promoted rung
    needs its own exporter (ADR-0020 paso 10 registers whatever the gate actually left
    serving, whatever model that is).
    """
    layout = _layout(tmp_path, train_frame(), promote=True)

    with pytest.raises(ValueError, match="only fits the climatology"):
        registration.serve_name(layout)


def test_a_gate_that_did_not_promote_serves_the_best_validation_baseline(tmp_path: Path) -> None:
    """The negative of the refusal above: `promote=false` is a valid gate answer and it
    serves `baseline.name`, exactly what `GateRun.served` returns (docs/08-ml.md §M2
    "Línea base servida", D-T0.5)."""
    layout = _layout(tmp_path, train_frame(), promote=False)

    assert registration.serve_name(layout) == BASELINE_RUNG
    assert registration.serve_name(layout) != CANDIDATE_RUNG


def test_a_receipt_without_the_gate_answer_is_refused(tmp_path: Path) -> None:
    """A receipt that names no rung and no verdict is not an answer: serving one of the two
    at random would be registering a version no gate ever chose."""
    layout = _layout(tmp_path, train_frame(), promote=None)
    path = gate_run.receipt_path(layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"candidate": CANDIDATE_RUNG}) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="promote"):
        registration.serve_name(layout)


def test_another_registered_version_does_not_block_this_one(tmp_path: Path) -> None:
    """The idempotence check is on the version string, not on "a row exists": yesterday's
    baseline does not stop today's version from being registered."""
    layout = _layout(tmp_path, train_frame(), promote=False)
    yesterday = replace(_registered_version(), version="2026-10-04-climatology_month")
    repository = RecordingRepository([yesterday])
    client = RecordingS3()

    version = _register(layout, repository, client)

    assert version.version == VERSION
    assert repository.inserted == [version]
    assert len(client.objects) == 1


def test_the_serve_name_never_rewrites_the_receipt(tmp_path: Path) -> None:
    """The receipt is the record of one read of the blocked block: registration reads it
    and leaves it alone."""
    layout = _layout(tmp_path, train_frame(), promote=False)
    before = gate_run.receipt_path(layout).read_bytes()

    registration.serve_name(layout)

    assert gate_run.receipt_path(layout).read_bytes() == before


def test_the_gate_run_date_comes_from_the_receipt(tmp_path: Path) -> None:
    layout = _layout(tmp_path, train_frame(), promote=False)

    assert registration.gate_run_on(layout) == DATE


def test_the_row_is_a_served_baseline_without_thresholds(tmp_path: Path) -> None:
    """D-T9.1 and D-T9.2: `is_baseline`, not promoted, and `thresholds={}` — the model
    card's cuts belong to the calibrated candidate, and a threshold invented for a
    climatology is one nothing ever validated (docs/08 §M2 "Severidad")."""
    layout = _layout(tmp_path, train_frame(), promote=False)
    repository = RecordingRepository()

    version = _register(layout, repository, RecordingS3())

    assert version.name == "risk_flood"
    assert version.version == VERSION
    assert version.is_baseline is True
    assert version.promoted is False
    assert version.thresholds == {}
    assert version.metrics is None
    assert version.artifact_uri == f"s3://ml-artifacts/{KEY}"
    assert version.artifact_sha256 == registration.artifact_sha256(
        registration.export_artifact(VERSION, train_frame())
    )
    assert version.dataset_hash == DATASET_SHA256
    assert version.git_commit == GIT_COMMIT
    assert repository.inserted == [version]


def test_the_baseline_metrics_are_the_four_figures_of_the_register(tmp_path: Path) -> None:
    """docs/04 §Riesgo: a served baseline answers with `baseline_metrics`, so the row
    carries the register's own four numbers rather than a second estimation of them."""
    layout = _layout(tmp_path, train_frame(), promote=False)

    version = _register(layout, RecordingRepository(), RecordingS3())

    with LOG_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == LOG_COLUMNS
        rows = [row for row in reader if row["model"] == BASELINE_RUNG]
    expected = rows[-1]
    assert version.baseline_metrics == {
        "pr_auc": float(expected["val_pr_auc"]),
        "pr_auc_ci_low": float(expected["val_pr_auc_ci_low"]),
        "pr_auc_ci_high": float(expected["val_pr_auc_ci_high"]),
        "brier": float(expected["val_brier"]),
    }


def test_a_register_with_no_row_for_the_served_rung_is_refused() -> None:
    with pytest.raises(ValueError, match="a_rung_that_never_ran"):
        registration.baseline_metrics("a_rung_that_never_ran", path=LOG_PATH)


def test_the_artifact_is_uploaded_under_its_key_with_the_sha_of_the_body(tmp_path: Path) -> None:
    """docs/03 §Integridad del artefacto: the reader compares `artifact_sha256` against the
    bytes it downloaded, so the row's digest and the uploaded body are the same bytes."""
    layout = _layout(tmp_path, train_frame(), promote=False)
    client = RecordingS3()

    version = _register(layout, RecordingRepository(), client)

    assert len(client.objects) == 1
    (call,) = client.objects
    assert call["Bucket"] == "ml-artifacts"
    assert call["Key"] == KEY
    assert call["ContentType"] == "application/json"
    body = call["Body"]
    assert isinstance(body, bytes)
    assert version.artifact_sha256 == registration.artifact_sha256(body)
    assert json.loads(body)["version"] == VERSION


def test_a_version_already_registered_is_not_inserted_again(tmp_path: Path) -> None:
    """ADR-0020 paso 10 reads the table before writing (docs/08 §Reglas de gobierno,
    "Reversión": los artefactos no se borran), so a rerun of the same gate run leaves one
    row and does not write anything else."""
    layout = _layout(tmp_path, train_frame(), promote=False)
    already = _registered_version()
    repository = RecordingRepository([already])
    client = RecordingS3()

    version = _register(layout, repository, client)

    assert version is already
    assert repository.inserted == []
    assert client.objects == []


def test_a_dry_run_prints_the_plan_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = _layout(tmp_path, train_frame(), promote=False)

    assert registration.main(["--dry-run"], layout=layout) == 0

    printed = capsys.readouterr().out
    assert VERSION in printed
    assert KEY in printed
    assert registration.artifact_sha256(b"") not in printed
    assert gate_run.receipt_path(layout).read_text(encoding="utf-8").count("promote") == 1


def test_a_refused_run_answers_1_and_explains_itself(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = _layout(tmp_path, train_frame(), promote=None)

    assert registration.main([], layout=layout) == 1
    assert "gate" in capsys.readouterr().err


def test_the_real_s3_client_is_built_path_style_and_against_the_configured_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The uploader itself, built for real, because every other test doubles it.

    A client that is never constructed cannot fail to construct: `botocore` rejects an
    unknown `Config` keyword at build time, and this client is only ever built by the CLI
    against a real bucket. Pinning it here is what turns "the run against MinIO raised
    `TypeError: Got unexpected keyword argument`" into a unit failure. No request is sent:
    constructing a client opens no connection.
    """
    monkeypatch.setenv("TECHCAMP_S3_PUBLIC_URL", "http://minio.test:9000")
    monkeypatch.setenv("TECHCAMP_S3_BUCKET", "logbook-photos")

    client = registration.s3_client()

    assert client.meta.endpoint_url == "http://minio.test:9000"
    # Path style, or a virtual-hosted URL would hand the emulator a host that does not
    # resolve (`logbook-photos.localhost:9000`); SigV4, which is what the store signs.
    assert client.meta.config.s3["addressing_style"] == "path"
    assert client.meta.config.signature_version == "s3v4"


def test_the_client_the_registry_will_read_is_never_the_public_one() -> None:
    """The negative of the endpoint decision, stated where the uploader is built.

    `ml/` runs on the developer's host, outside the compose network, so the PUBLIC url is
    the one that reaches the store from here. The worker that later reads the same object
    goes through `s3_internal_url()` instead, because inside a container `localhost` is
    the container (D-T9.5).
    """
    assert s3_public_url().startswith("http://localhost:9000")
    assert s3_internal_url().startswith("http://minio:9000")


def test_registration_takes_the_claim_on_the_version_before_it_reads_or_writes(
    tmp_path: Path,
) -> None:
    """The claim is taken FIRST, before the existence read and before the upload.

    Registration is a read followed by a write, and two processes doing that at once both
    read "nothing registered": two rows claiming one version, and — worse — two uploads
    to the same object key, where the second is what the bucket keeps. The surviving
    row's `artifact_sha256` would then describe bytes that are not there, and every
    prediction of that version is refused as unverified (docs/03-modelo-datos.md
    §Integridad del artefacto). The order is the whole fix, so it is what is pinned.
    """
    layout = _layout(tmp_path, train_frame(), promote=False)
    store = RecordingRepository()
    client = RecordingS3()

    asyncio.run(
        registration.register(  # type: ignore[arg-type]
            None, layout=layout, bucket="ml-artifacts", repository=store, client=client
        )
    )

    assert store.locks == [("risk_flood", VERSION)]
    assert len(store.inserted) == 1
    assert len(client.objects) == 1


def test_a_version_that_is_already_registered_still_takes_the_claim_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """The negative of the order above: holding the claim is not itself a write.

    A re-run of a version that is already there returns the row it found and uploads
    nothing, so the object the committed row describes is never overwritten (docs/08
    §Reglas de gobierno, "Reversión": los artefactos no se borran).
    """
    layout = _layout(tmp_path, train_frame(), promote=False)
    already = _registered_version()
    store = RecordingRepository(existing=[already])
    client = RecordingS3()

    returned = asyncio.run(
        registration.register(  # type: ignore[arg-type]
            None, layout=layout, bucket="ml-artifacts", repository=store, client=client
        )
    )

    assert returned is already
    assert store.locks == [("risk_flood", VERSION)]
    assert store.inserted == []
    assert client.objects == []
