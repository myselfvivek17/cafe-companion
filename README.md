# Café Companion
The café that adapts to why you came. FastAPI + plain HTML/JS, SQLAlchemy, Gemini (Vertex AI) with rule-based fallback.

## Run locally
```
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx
.venv/bin/uvicorn app.main:app --port 8500
```
Open `/` (landing), `/staff` (press "Seed demo visitors", then "Run matching"), `/t/1` (customer).
SQLite file `cafe.db` is used unless `DATABASE_URL` is set. Gemini is used if `GOOGLE_CLOUD_PROJECT` is set
(plus ADC credentials; `GOOGLE_CLOUD_LOCATION` default `global`, `GEMINI_MODEL` default `gemini-2.5-flash`); otherwise/on error, rules are used.

## Test
`.venv/bin/pytest -q`

## Deploy (Cloud Run + Cloud SQL Postgres)
```
export GOOGLE_CLOUD_PROJECT=... CLOUDSQL_INSTANCE=proj:asia-south1:inst
export DATABASE_URL='postgresql+pg8000://USER:PASS@/DB?unix_sock=/cloudsql/proj:asia-south1:inst/.s.PGSQL.5432'
./deploy.sh
```
The Cloud Run service account needs Cloud SQL Client and Vertex AI User roles.

## Privacy
Matches reveal first name + table only, and only after everyone accepts. No photos/age/gender.
