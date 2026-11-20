# Shared AI-EFFECT workflow UI. Build context: this directory.
# Each workflow mounts its use-case directory at USE_CASE_DIR (ui.json,
# connections.json, export/ and optional ui/ plugins).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY templates ./templates
COPY static ./static

ENV HOST=0.0.0.0
ENV PORT=8080
ENV DATA_DIR=/data
ENV USE_CASE_DIR=/workspace/use-case
ENV ORCHESTRATOR_URL=http://ai-effect-orchestrator:8000

EXPOSE 8080

CMD ["python", "app.py"]
