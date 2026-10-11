FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY static static
ENV GOOGLE_CLOUD_LOCATION=global
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
