FROM python:3.12-slim

WORKDIR /app

COPY services/config_assistant/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ ./common/
COPY services/explainability_runner/toolbox/ai_explainability/__init__.py ./toolbox/ai_explainability/
COPY services/explainability_runner/toolbox/ai_explainability/capabilities.py ./toolbox/ai_explainability/
COPY services/config_assistant/service.py ./

ENV HOST=0.0.0.0
ENV PORT=8080
ENV DATA_DIR=/data
ENV PYTHONPATH=/app/toolbox:/app
ENV OLLAMA_URL=http://ai-explainability-ollama:11434
ENV OLLAMA_MODEL=qwen2.5:1.5b

EXPOSE 8080

CMD ["python", "service.py"]
