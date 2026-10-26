"""`python -m techcamp.init_buckets`: the seminar profile's object storage buckets.

The logbook photo bucket and the ML artifact bucket (ADR-0018, ADR-0021) have to
exist before the first photo is presigned and before the daily flood job
downloads its artifact: MinIO creates neither on its own.

The buckets are created with the same `boto3` the API signs with, from the
server's own image, so the seminar stack does not need a `minio/mc` container:
`quay.io/minio/mc` denies anonymous pulls, and an init container whose image
cannot be pulled is a stack that does not come up (#214). Nothing is downloaded
from a registry to run this.

It is best-effort on purpose. The store may still be starting, and a store that
never answers must not keep the stack down: the buckets exist as soon as the
store answers, and the exit code says whether they do.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from techcamp.shared.config import (
    s3_access_key,
    s3_bucket,
    s3_internal_url,
    s3_ml_bucket,
    s3_region,
    s3_secret_key,
)

logger = logging.getLogger(__name__)

ATTEMPTS = 30
"""How many times the store is asked before the init gives up: a minute of
waiting, which covers a store that is still booting."""

RETRY_SECONDS = 2

_ALREADY_THERE = frozenset({"BucketAlreadyOwnedByYou", "BucketAlreadyExists"})
"""The two answers a store gives when the bucket is there already: the init is a
repair, not a race, so restarting the stack over a kept volume is a no-op."""


def _client() -> Any:
    """The S3 client the API signs with, pointed at the in-network endpoint."""
    access_key = s3_access_key()
    secret_key = s3_secret_key()
    if access_key is None or secret_key is None:
        raise RuntimeError(
            "the object storage credentials are not set; the seminar profile's defaults "
            "are the MinIO root user of infra/compose.yaml"
        )
    return boto3.client(
        "s3",
        endpoint_url=s3_internal_url(),
        region_name=s3_region(),
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _ensure_bucket(client: Any, bucket: str) -> None:
    """Create the bucket if it is not there yet."""
    try:
        client.create_bucket(Bucket=bucket)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code in _ALREADY_THERE:
            return
        raise


def main() -> int:
    """Create the buckets, waiting for the store to answer. Zero when the store
    never answered (the stack comes up without the object store), one when it
    answered and the creation failed (a real misconfiguration, worth stopping
    for)."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    buckets = (s3_bucket(), s3_ml_bucket())
    client = _client()
    for attempt in range(1, ATTEMPTS + 1):
        try:
            for bucket in buckets:
                _ensure_bucket(client, bucket)
        except (BotoCoreError, ClientError) as error:
            logger.warning(
                "the object store did not answer yet (attempt %d/%d): %s", attempt, ATTEMPTS, error
            )
            time.sleep(RETRY_SECONDS)
            continue
        logger.info("the buckets %s and %s are ready", *buckets)
        return 0
    logger.error(
        "the object store never answered; the buckets %s and %s were not created. "
        "The stack comes up without them: the photo upload is the only feature "
        "that needs them.",
        *buckets,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
