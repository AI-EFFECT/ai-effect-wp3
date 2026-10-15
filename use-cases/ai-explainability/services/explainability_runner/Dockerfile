FROM python:3.12-slim

WORKDIR /app

COPY services/explainability_runner/requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
	&& pip install --no-cache-dir -r requirements.txt

COPY common/ ./common/
COPY services/explainability_runner/toolbox/ ./toolbox/
COPY services/explainability_runner/service.py ./

ENV HOST=0.0.0.0
ENV PORT=8080
ENV DATA_DIR=/data
ENV ASSETS_DIR=/assets
ENV PYTHONPATH=/app/toolbox:/app

EXPOSE 8080

CMD ["python", "service.py"]