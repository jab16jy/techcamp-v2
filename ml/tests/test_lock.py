"""Tests for the ML harness lock check (ADR-0020, #192)."""

from pathlib import Path

import pytest
from techcamp_ml.harness.lock import (
    LockCheckError,
    check_harness_lock,
    generate_manifest,
    update_manifest_file,
    verify_manifest,
)


def test_empty_manifest_passes(tmp_path: Path) -> None:
    manifest = tmp_path / "LOCK.sha256"
    manifest.write_text("", encoding="utf-8")

    errors = verify_manifest(manifest, repo_root=tmp_path)
    assert errors == []
    # check_harness_lock does not raise when there are no errors
    check_harness_lock(manifest, repo_root=tmp_path)


def test_manifest_matching_files_passes(tmp_path: Path) -> None:
    file_a = tmp_path / "ml/harness/file_a.py"
    file_a.parent.mkdir(parents=True, exist_ok=True)
    file_a.write_text("print('hello')\n", encoding="utf-8")

    manifest = tmp_path / "ml/harness/LOCK.sha256"
    update_manifest_file(manifest, target_dir=tmp_path / "ml/harness", repo_root=tmp_path)

    errors = verify_manifest(manifest, repo_root=tmp_path)
    assert errors == []
    check_harness_lock(manifest, repo_root=tmp_path)


def test_manifest_missing_file_fails(tmp_path: Path) -> None:
    manifest = tmp_path / "LOCK.sha256"
    # An entry for a non-existent file
    manifest.write_text(
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  ml/harness/missing.py\n",
        encoding="utf-8",
    )

    errors = verify_manifest(manifest, repo_root=tmp_path)
    assert len(errors) == 1
    assert "missing" in errors[0].lower() or "not found" in errors[0].lower()

    with pytest.raises(LockCheckError, match="ml/harness/missing.py"):
        check_harness_lock(manifest, repo_root=tmp_path)


def test_manifest_tampered_file_fails(tmp_path: Path) -> None:
    target_file = tmp_path / "ml/harness/split.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("original_content\n", encoding="utf-8")

    manifest = tmp_path / "ml/harness/LOCK.sha256"
    update_manifest_file(manifest, target_dir=tmp_path / "ml/harness", repo_root=tmp_path)

    # Verify passes initially
    assert verify_manifest(manifest, repo_root=tmp_path) == []

    # Modify the file (tamper)
    target_file.write_text("tampered_content\n", encoding="utf-8")

    # Negative assertion: verification must detect hash mismatch
    errors = verify_manifest(manifest, repo_root=tmp_path)
    assert len(errors) == 1
    assert "hash mismatch" in errors[0].lower() or "mismatch" in errors[0].lower()

    with pytest.raises(LockCheckError, match="ml/harness/split.py"):
        check_harness_lock(manifest, repo_root=tmp_path)


def test_manifest_ignores_comments_and_empty_lines(tmp_path: Path) -> None:
    target_file = tmp_path / "ml/harness/gate.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("gate_code\n", encoding="utf-8")

    manifest = tmp_path / "ml/harness/LOCK.sha256"
    manifest_content = generate_manifest(tmp_path / "ml/harness", repo_root=tmp_path)
    # Add comments and blank lines
    decorated_content = f"# Comment line\n\n{manifest_content}\n\n# Another comment\n"
    manifest.write_text(decorated_content, encoding="utf-8")

    errors = verify_manifest(manifest, repo_root=tmp_path)
    assert errors == []


def test_repo_harness_lock() -> None:
    """The live harness manifest in the repo must pass check_harness_lock."""
    # Find repo root from this test file
    repo_root = Path(__file__).resolve().parents[2]
    manifest = repo_root / "ml/harness/LOCK.sha256"
    assert manifest.exists(), f"Manifest file {manifest} must exist"

    errors = verify_manifest(manifest, repo_root=repo_root)
    assert errors == [], f"Harness lock check failed with errors: {errors}"
    check_harness_lock(manifest, repo_root=repo_root)
