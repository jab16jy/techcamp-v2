"""Lock manifest generation and verification for the ML harness (ADR-0020, #192).

Enforces that the harness, locked test split, and promotion gate have not been
tampered with since human approval.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

DEFAULT_MANIFEST_REL_PATH = Path("ml/harness/LOCK.sha256")
DEFAULT_HARNESS_REL_DIR = Path("ml/harness")


class LockCheckError(Exception):
    """Raised when the harness lock manifest verification fails."""


def compute_sha256(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def find_repo_root(anchor: Path | None = None) -> Path:
    """Find repository root containing pyproject.toml or .git upwards from anchor."""
    start = (anchor or Path.cwd()).resolve()
    for directory in [start, *start.parents]:
        if (directory / ".git").exists() or (directory / "server/pyproject.toml").exists():
            return directory
    return start


def verify_manifest(
    manifest_path: Path,
    repo_root: Path | None = None,
) -> list[str]:
    """Verify that every file listed in the manifest exists and matches its hash.

    Returns a list of error messages (empty list if all entries pass).
    """
    manifest_path = manifest_path.resolve()
    if not manifest_path.is_file():
        return [f"Manifest file not found: {manifest_path}"]

    effective_root = repo_root.resolve() if repo_root is not None else find_repo_root(manifest_path)
    errors: list[str] = []

    content = manifest_path.read_text(encoding="utf-8")
    for line_num, line in enumerate(content.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Standard sha256sum format: "<hash>  <path>" or "<hash> <path>"
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            errors.append(f"Invalid format at {manifest_path}:{line_num}: '{line}'")
            continue

        expected_hash, rel_path = parts
        rel_path = rel_path.strip()

        target_file = effective_root / rel_path
        if not target_file.is_file():
            errors.append(f"Missing file: {rel_path}")
            continue

        actual_hash = compute_sha256(target_file)
        if actual_hash.lower() != expected_hash.lower():
            errors.append(
                f"Hash mismatch for {rel_path}: expected {expected_hash}, got {actual_hash}"
            )

    return errors


def check_harness_lock(
    manifest_path: Path,
    repo_root: Path | None = None,
) -> None:
    """Verify the manifest and raise LockCheckError if any check fails."""
    errors = verify_manifest(manifest_path, repo_root=repo_root)
    if errors:
        raise LockCheckError("Harness lock verification failed:\n" + "\n".join(errors))


def generate_manifest(
    target_dir: Path,
    repo_root: Path | None = None,
    exclude_names: set[str] | None = None,
) -> str:
    """Generate SHA-256 manifest content for all regular files in target_dir.

    Files are sorted by repo-relative path. Files matching exclude_names or located
    in hidden or cache directories are omitted.
    """
    target_dir = target_dir.resolve()
    effective_root = repo_root.resolve() if repo_root is not None else find_repo_root(target_dir)
    exclusions = exclude_names if exclude_names is not None else {"LOCK.sha256", ".DS_Store"}

    entries: list[tuple[str, str]] = []
    if target_dir.exists():
        for path in target_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.name in exclusions:
                continue
            # Ignore cache or hidden paths
            parts = path.relative_to(target_dir).parts
            if any(part.startswith(".") or part == "__pycache__" for part in parts):
                continue

            rel_path = path.relative_to(effective_root).as_posix()
            file_hash = compute_sha256(path)
            entries.append((rel_path, file_hash))

    entries.sort(key=lambda item: item[0])
    lines = [f"{file_hash}  {rel_path}" for rel_path, file_hash in entries]
    return "\n".join(lines) + ("\n" if lines else "")


def update_manifest_file(
    manifest_path: Path,
    target_dir: Path | None = None,
    repo_root: Path | None = None,
) -> None:
    """Generate and write the manifest file."""
    manifest_path = manifest_path.resolve()
    effective_target = target_dir.resolve() if target_dir is not None else manifest_path.parent
    content = generate_manifest(effective_target, repo_root=repo_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ML harness lock manifest manager")
    parser.add_argument(
        "action",
        choices=["check", "generate"],
        default="check",
        nargs="?",
        help="Action to perform: 'check' verifies, 'generate' regenerates manifest",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to manifest file (defaults to ml/harness/LOCK.sha256)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Path to repository root",
    )

    args = parser.parse_args(argv)
    root = find_repo_root(args.repo_root)
    manifest = (args.manifest or (root / DEFAULT_MANIFEST_REL_PATH)).resolve()

    if args.action == "generate":
        harness_dir = root / DEFAULT_HARNESS_REL_DIR
        update_manifest_file(manifest, target_dir=harness_dir, repo_root=root)
        print(f"Updated harness lock manifest at {manifest.relative_to(root)}")
        return 0

    try:
        check_harness_lock(manifest, repo_root=root)
        print(f"Harness lock check passed for {manifest.relative_to(root)}")
        return 0
    except LockCheckError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
