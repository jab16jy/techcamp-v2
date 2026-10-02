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


def test_empty_manifest_passes_when_no_tracked_files(tmp_path: Path) -> None:
    manifest = tmp_path / "LOCK.sha256"
    manifest.write_text("", encoding="utf-8")

    errors = verify_manifest(manifest, repo_root=tmp_path, tracked_dirs=())
    assert errors == []
    check_harness_lock(manifest, repo_root=tmp_path, tracked_dirs=())


def test_manifest_matching_files_passes(tmp_path: Path) -> None:
    file_a = tmp_path / "ml/harness/file_a.py"
    file_a.parent.mkdir(parents=True, exist_ok=True)
    file_a.write_text("print('hello')\n", encoding="utf-8")

    file_b = tmp_path / "ml/src/techcamp_ml/harness/lock.py"
    file_b.parent.mkdir(parents=True, exist_ok=True)
    file_b.write_text("print('lock')\n", encoding="utf-8")

    tracked_dirs = (tmp_path / "ml/harness", tmp_path / "ml/src/techcamp_ml/harness")
    manifest = tmp_path / "ml/harness/LOCK.sha256"
    update_manifest_file(manifest, target_dirs=tracked_dirs, repo_root=tmp_path)

    errors = verify_manifest(manifest, repo_root=tmp_path, tracked_dirs=tracked_dirs)
    assert errors == []
    check_harness_lock(manifest, repo_root=tmp_path, tracked_dirs=tracked_dirs)


def test_manifest_missing_file_fails(tmp_path: Path) -> None:
    manifest = tmp_path / "LOCK.sha256"
    # An entry for a non-existent file
    manifest.write_text(
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  ml/harness/missing.py\n",
        encoding="utf-8",
    )

    errors = verify_manifest(manifest, repo_root=tmp_path, tracked_dirs=())
    assert len(errors) == 1
    assert "missing" in errors[0].lower() or "not found" in errors[0].lower()

    with pytest.raises(LockCheckError, match="ml/harness/missing.py"):
        check_harness_lock(manifest, repo_root=tmp_path, tracked_dirs=())


def test_manifest_tampered_file_fails(tmp_path: Path) -> None:
    target_file = tmp_path / "ml/harness/split.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("original_content\n", encoding="utf-8")

    manifest = tmp_path / "ml/harness/LOCK.sha256"
    update_manifest_file(manifest, target_dirs=[tmp_path / "ml/harness"], repo_root=tmp_path)

    # Verify passes initially
    errors = verify_manifest(manifest, repo_root=tmp_path, tracked_dirs=[tmp_path / "ml/harness"])
    assert errors == []

    # Modify the file (tamper)
    target_file.write_text("tampered_content\n", encoding="utf-8")

    # Negative assertion: verification must detect hash mismatch
    errors = verify_manifest(manifest, repo_root=tmp_path, tracked_dirs=[tmp_path / "ml/harness"])
    assert len(errors) == 1
    assert "hash mismatch" in errors[0].lower() or "mismatch" in errors[0].lower()

    with pytest.raises(LockCheckError, match="ml/harness/split.py"):
        check_harness_lock(manifest, repo_root=tmp_path, tracked_dirs=[tmp_path / "ml/harness"])


def test_manifest_unlisted_file_in_tracked_dir_fails(tmp_path: Path) -> None:
    file_a = tmp_path / "ml/harness/split.py"
    file_a.parent.mkdir(parents=True, exist_ok=True)
    file_a.write_text("split_code\n", encoding="utf-8")

    file_b = tmp_path / "ml/src/techcamp_ml/harness/unlisted.py"
    file_b.parent.mkdir(parents=True, exist_ok=True)
    file_b.write_text("unlisted_code\n", encoding="utf-8")

    tracked_dirs = (tmp_path / "ml/harness", tmp_path / "ml/src/techcamp_ml/harness")
    manifest = tmp_path / "ml/harness/LOCK.sha256"
    # Only list file_a in the manifest
    update_manifest_file(manifest, target_dirs=[tmp_path / "ml/harness"], repo_root=tmp_path)

    # Negative assertion: an unlisted file in a tracked harness directory must fail verification
    errors = verify_manifest(manifest, repo_root=tmp_path, tracked_dirs=tracked_dirs)
    assert any("unlisted" in err.lower() and "unlisted.py" in err for err in errors)

    with pytest.raises(LockCheckError, match="unlisted.py"):
        check_harness_lock(manifest, repo_root=tmp_path, tracked_dirs=tracked_dirs)


def test_manifest_unlisted_file_in_ml_harness_fails(tmp_path: Path) -> None:
    file_extra = tmp_path / "ml/harness/extra.py"
    file_extra.parent.mkdir(parents=True, exist_ok=True)
    file_extra.write_text("extra_code\n", encoding="utf-8")

    tracked_dirs = (tmp_path / "ml/harness", tmp_path / "ml/src/techcamp_ml/harness")
    manifest = tmp_path / "ml/harness/LOCK.sha256"
    manifest.write_text("", encoding="utf-8")

    # Negative assertion: an unlisted file in ml/harness must fail verification
    errors = verify_manifest(manifest, repo_root=tmp_path, tracked_dirs=tracked_dirs)
    assert any("unlisted" in err.lower() and "ml/harness/extra.py" in err for err in errors)

    with pytest.raises(LockCheckError, match="ml/harness/extra.py"):
        check_harness_lock(manifest, repo_root=tmp_path, tracked_dirs=tracked_dirs)


def test_manifest_ignores_comments_and_empty_lines(tmp_path: Path) -> None:
    target_file = tmp_path / "ml/harness/gate.py"
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("gate_code\n", encoding="utf-8")

    manifest = tmp_path / "ml/harness/LOCK.sha256"
    manifest_content = generate_manifest([tmp_path / "ml/harness"], repo_root=tmp_path)
    # Add comments and blank lines
    decorated_content = f"# Comment line\n\n{manifest_content}\n\n# Another comment\n"
    manifest.write_text(decorated_content, encoding="utf-8")

    errors = verify_manifest(manifest, repo_root=tmp_path, tracked_dirs=[tmp_path / "ml/harness"])
    assert errors == []


def test_repo_harness_lock() -> None:
    """The live harness manifest in the repo must pass check_harness_lock."""
    repo_root = Path(__file__).resolve().parents[2]
    manifest = repo_root / "ml/harness/LOCK.sha256"
    assert manifest.exists(), f"Manifest file {manifest} must exist"

    errors = verify_manifest(manifest, repo_root=repo_root)
    assert errors == [], f"Harness lock check failed with errors: {errors}"
    check_harness_lock(manifest, repo_root=repo_root)
