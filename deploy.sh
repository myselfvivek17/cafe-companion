#!/usr/bin/env bash
# Required env: GOOGLE_CLOUD_PROJECT, CLOUDSQL_INSTANCE (project:region:instance), DATABASE_URL
# DATABASE_URL e.g. postgresql+pg8000://USER:PASS@/DB?unix_sock=/cloudsql/$CLOUDSQL_INSTANCE/.s.PGSQL.5432
set -euo pipefail
: "${GOOGLE_CLOUD_PROJECT:?}" "${CLOUDSQL_INSTANCE:?}" "${DATABASE_URL:?}"
gcloud run deploy cafe-companion --source . --region asia-south1 \
  --project "$GOOGLE_CLOUD_PROJECT" --allow-unauthenticated \
  --add-cloudsql-instances "$CLOUDSQL_INSTANCE" \
  --min-instances 0 --max-instances 1 \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT,GOOGLE_CLOUD_LOCATION=global,GEMINI_MODEL=${GEMINI_MODEL:-gemini-2.5-flash}" \
  --update-env-vars "^@^DATABASE_URL=$DATABASE_URL"
