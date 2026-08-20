"""Daily Supabase (Postgres) backup job.

Dumps the database via `pg_dump` (custom format, -Fc — compressed and restorable
selectively via `pg_restore`) and uploads the result to a GCS bucket. Designed to run
as a Cloud Run Job on a Cloud Scheduler trigger (see doc/backup-setup.md), but works
identically run locally for a one-off manual backup.

Retention is handled by a GCS bucket lifecycle rule (age-based auto-delete), not by
this script — that way old backups still get cleaned up even on a day this job fails
to run, and there's one less thing for the script itself to get wrong.

Required env vars:
  SUPABASE_DB_URL  -- same Postgres connection string the app itself uses
  GCS_BUCKET_NAME  -- destination bucket, e.g. "oura-studio-backups"

Exits non-zero on any failure (pg_dump error, empty dump, upload error) so a Cloud Run
Job execution is correctly marked FAILED and shows up in monitoring/alerting.
"""

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from google.cloud import storage


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"FATAL: required env var {name} is not set", file=sys.stderr)
        sys.exit(1)
    return value


def run_pg_dump(db_url: str, dest_path: str) -> None:
    print(f"Running pg_dump -> {dest_path}")
    result = subprocess.run(
        ["pg_dump", db_url, "-Fc", "-f", dest_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"FATAL: pg_dump failed (exit {result.returncode})", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    if result.stderr:
        # pg_dump can print non-fatal warnings on stderr even on success (exit 0) --
        # surface them in logs without failing the job.
        print(f"pg_dump warnings:\n{result.stderr}")

    size = os.path.getsize(dest_path)
    if size == 0:
        print("FATAL: pg_dump produced an empty file", file=sys.stderr)
        sys.exit(1)
    print(f"pg_dump OK -- {size:,} bytes")


def upload_to_gcs(local_path: str, bucket_name: str, blob_name: str) -> None:
    print(f"Uploading to gs://{bucket_name}/{blob_name}")
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(local_path)
    print("Upload OK")


def main() -> None:
    db_url = _env("SUPABASE_DB_URL")
    bucket_name = _env("GCS_BUCKET_NAME")

    now = datetime.now(timezone.utc)
    blob_name = f"backups/oura-supabase-{now.strftime('%Y%m%d-%H%M%S')}.dump"

    with tempfile.TemporaryDirectory() as tmp_dir:
        local_path = os.path.join(tmp_dir, "backup.dump")
        run_pg_dump(db_url, local_path)
        upload_to_gcs(local_path, bucket_name, blob_name)

    print(f"Backup complete: gs://{bucket_name}/{blob_name}")


if __name__ == "__main__":
    main()
