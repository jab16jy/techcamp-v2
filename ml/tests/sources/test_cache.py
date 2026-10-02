"""The raw cache and its provenance manifest (docs/08 "Reproducible o no existe")."""

from pathlib import Path

from techcamp_ml.sources.cache import MANIFEST_COLUMNS, read_manifest, save_raw
from techcamp_ml.sources.layout import Layout


def test_save_raw_records_the_url_the_fetch_date_and_the_hash(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    save_raw(layout, "labels", "wwkg.json", "https://example.test/a", b'{"a": 1}')

    manifest = read_manifest(layout)
    assert tuple(manifest.columns) == MANIFEST_COLUMNS
    assert len(manifest) == 1
    row = manifest.iloc[0]
    assert row["source"] == "labels"
    assert row["file"] == "wwkg.json"
    assert row["url"] == "https://example.test/a"
    assert len(str(row["fetched_at"])) == len("2026-10-02T03:04:05Z")
    # The hash is of the bytes, so a different body can never share the row.
    assert row["sha256"] != "0" * 64

    other = save_raw(layout, "labels", "other.json", "https://example.test/b", b"{}")
    assert other.sha256 != row["sha256"]
    assert len(read_manifest(layout)) == 2


def test_saving_the_same_file_twice_keeps_one_row(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    save_raw(layout, "labels", "wwkg.json", "https://example.test/a", b"{}")
    save_raw(layout, "labels", "wwkg.json", "https://example.test/a?retry=2", b"{}")

    manifest = read_manifest(layout)
    assert len(manifest) == 1, "a re-fetch of the same file must not duplicate its provenance row"
    assert manifest.iloc[0]["url"] == "https://example.test/a?retry=2"


def test_a_missing_raw_copy_raises_instead_of_reading_nothing(tmp_path: Path) -> None:
    from techcamp_ml.sources.cache import read_raw

    try:
        read_raw(Layout(tmp_path), "labels", "absent.json")
    except FileNotFoundError as error:
        assert "absent.json" in str(error)
    else:  # pragma: no cover - the negative assertion
        raise AssertionError("reading a raw copy that was never fetched must raise")


def test_a_rate_limited_response_waits_for_the_window_it_asks_for() -> None:
    import httpx

    from techcamp_ml.sources.fetching import RETRYABLE_STATUSES, retry_delay

    rate_limited = httpx.Response(429, headers={"Retry-After": "42"})
    assert 429 in RETRYABLE_STATUSES, "a throttled host must be retried, not cached as a source"
    assert retry_delay(rate_limited, attempt=0) == 42.0

    # The negative case: a client error carries no Retry-After and is not retried.
    assert 400 not in RETRYABLE_STATUSES
    assert retry_delay(httpx.Response(400), attempt=0) == 0.0
    assert retry_delay(httpx.Response(503), attempt=2) > retry_delay(httpx.Response(503), attempt=1)


def test_the_throttle_counts_coordinates_not_requests() -> None:
    import pytest

    from techcamp_ml.sources.fetching import interval_seconds

    # Open-Meteo bills one call per coordinate, so a 100-coordinate request needs a
    # far wider gap than a single-point one.
    assert interval_seconds(1) < interval_seconds(25)
    assert interval_seconds(100) - interval_seconds(0) == pytest.approx(
        2 * (interval_seconds(50) - interval_seconds(0))
    )

    import httpx

    from techcamp_ml.sources.fetching import retry_delay

    # A 429 without Retry-After waits the minute the host names in its message.
    assert retry_delay(httpx.Response(429), attempt=0) == 60.0
