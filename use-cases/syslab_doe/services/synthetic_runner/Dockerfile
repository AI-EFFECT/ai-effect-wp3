FROM python:3.11-slim

WORKDIR /app

COPY syslab_doe/services/synthetic_runner/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ ./common/
COPY syslab_doe/services/synthetic_runner/synthetic_runner.py .
COPY syslab_doe/services/synthetic_runner/service.py .

EXPOSE 8080

CMD ["python", "service.py"]
