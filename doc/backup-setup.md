# Supabase Daily Backup — Cloud Run Job + Cloud Scheduler Setup

Deploys `scripts/backup_supabase.py` as a **Cloud Run Job** that `pg_dump`s the
Supabase database and uploads the dump to a GCS bucket, triggered daily by
**Cloud Scheduler**. Old backups are cleaned up by a GCS bucket lifecycle rule,
not by the script itself.

Files involved:
- `scripts/backup_supabase.py` — the backup logic
- `Dockerfile.backup` — image (installs `postgresql-client-17` to match Supabase's Postgres 17)
- `requirements-backup.txt` — Python deps for the image

Run every command below from the `backend/` directory, with `gcloud` already
authenticated (`gcloud auth login`) and pointed at the target project:

```bash
export PROJECT_ID=your-gcp-project-id
export REGION=asia-southeast2   # or whatever region you use for Cloud Run
gcloud config set project "$PROJECT_ID"
```

## 1. Enable required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com
```

## 2. Create the backup bucket + retention rule

```bash
export BUCKET_NAME=oura-studio-backups
gcloud storage buckets create "gs://$BUCKET_NAME" \
  --location="$REGION" \
  --uniform-bucket-level-access

# Auto-delete objects under backups/ after 30 days -- this is the ONLY retention
# mechanism; the script never deletes anything itself.
cat > /tmp/backup-lifecycle.json <<'EOF'
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {"age": 30, "matchesPrefix": ["backups/"]}
    }
  ]
}
EOF
gcloud storage buckets update "gs://$BUCKET_NAME" \
  --lifecycle-file=/tmp/backup-lifecycle.json
```

Adjust the `age` (days) to your desired retention window.

## 3. Store the database URL in Secret Manager

`pg_dump` needs a plain `postgresql://` connection string. **Do not reuse
`SUPABASE_DB_URL` from `.env` as-is** — the app's value is prefixed
`postgresql+psycopg2://` for SQLAlchemy, which `pg_dump` doesn't understand.
Strip the `+psycopg2` part before storing it:

```bash
# Take the value from .env / Supabase Project Settings > Database > Connection
# string (Session pooler variant), then drop "+psycopg2":
#   postgresql+psycopg2://postgres.<ref>:<password>@<host>:5432/postgres
#   -> postgresql://postgres.<ref>:<password>@<host>:5432/postgres

printf '%s' 'postgresql://postgres.your-project-ref:your-db-password@aws-0-region.pooler.supabase.com:5432/postgres' \
  | gcloud secrets create supabase-db-url --data-file=-
```

If the secret already exists, add a new version instead:
```bash
printf '%s' '...' | gcloud secrets versions add supabase-db-url --data-file=-
```

Use the **Session pooler** connection string (not the direct
`db.<ref>.supabase.co` host, which is IPv6-only and unreachable from Cloud
Run) — same rule as in `.env.example`.

## 4. Build and push the image

```bash
gcloud artifacts repositories create oura-backend \
  --repository-format=docker \
  --location="$REGION" \
  --description="Oura Studios backend images" 2>/dev/null || true  # skip if it already exists

gcloud builds submit \
  --tag "$REGION-docker.pkg.dev/$PROJECT_ID/oura-backend/supabase-backup" \
  -f Dockerfile.backup .
```

## 5. Create a dedicated service account

```bash
gcloud iam service-accounts create supabase-backup-job \
  --display-name="Supabase backup Cloud Run Job"

export SA_EMAIL=supabase-backup-job@$PROJECT_ID.iam.gserviceaccount.com

# Write access to the backup bucket only
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/storage.objectAdmin"

# Read access to the db-url secret
gcloud secrets add-iam-policy-binding supabase-db-url \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/secretmanager.secretAccessor"
```

## 6. Deploy the Cloud Run Job

```bash
gcloud run jobs create supabase-backup \
  --image="$REGION-docker.pkg.dev/$PROJECT_ID/oura-backend/supabase-backup" \
  --region="$REGION" \
  --service-account="$SA_EMAIL" \
  --set-env-vars="GCS_BUCKET_NAME=$BUCKET_NAME" \
  --set-secrets="SUPABASE_DB_URL=supabase-db-url:latest" \
  --max-retries=1 \
  --task-timeout=600
```

To pick up a newer image after a code change, redeploy with:
```bash
gcloud run jobs update supabase-backup \
  --image="$REGION-docker.pkg.dev/$PROJECT_ID/oura-backend/supabase-backup" \
  --region="$REGION"
```

## 7. Test it manually before scheduling

```bash
gcloud run jobs execute supabase-backup --region="$REGION" --wait
```

Check the job succeeded (`Succeeded` state) and that a new object showed up:
```bash
gcloud storage ls "gs://$BUCKET_NAME/backups/"
```

## 8. Schedule it daily with Cloud Scheduler

Cloud Scheduler triggers the job via the Cloud Run Jobs REST API using OIDC
auth from a scheduler-specific service account.

```bash
gcloud iam service-accounts create supabase-backup-scheduler \
  --display-name="Supabase backup Cloud Scheduler invoker"

export SCHEDULER_SA=supabase-backup-scheduler@$PROJECT_ID.iam.gserviceaccount.com

gcloud run jobs add-iam-policy-binding supabase-backup \
  --region="$REGION" \
  --member="serviceAccount:$SCHEDULER_SA" \
  --role="roles/run.invoker"

gcloud scheduler jobs create http supabase-backup-daily \
  --location="$REGION" \
  --schedule="0 3 * * *" \
  --uri="https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/supabase-backup:run" \
  --http-method=POST \
  --oauth-service-account-email="$SCHEDULER_SA"
```

`0 3 * * *` runs daily at 03:00 in the scheduler's configured timezone
(defaults to the project's App Engine timezone, usually UTC — pass
`--time-zone="Asia/Jakarta"` if you want it to run at 03:00 WIB instead).

## 9. Verify end-to-end

```bash
gcloud scheduler jobs run supabase-backup-daily --location="$REGION"
gcloud run jobs executions list --job=supabase-backup --region="$REGION"
```

Monitoring: Cloud Run Job executions that fail (non-zero exit from the
script — pg_dump error, empty dump, or upload error) show as `Failed` in
`gcloud run jobs executions list` and in the Cloud Console, which you can
alert on via a log-based metric or Cloud Monitoring alert policy on the
`run.googleapis.com/job/completed_execution_count` metric filtered to
`result_type="failed"`.

## Restoring from a backup

```bash
gcloud storage cp "gs://$BUCKET_NAME/backups/oura-supabase-<timestamp>.dump" .
pg_restore --dbname="postgresql://postgres.<ref>:<password>@<host>:5432/postgres" \
  --clean --if-exists oura-supabase-<timestamp>.dump
```

Use `pg_restore -l` on the dump first if you want to restore only specific
tables rather than the whole database.
