FROM python:3.11-slim

WORKDIR /app

COPY syslab_doe/services/doe_analysis/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ ./common/
COPY syslab_doe/services/doe_analysis/analysis.py .
COPY syslab_doe/services/doe_analysis/service.py .

EXPOSE 8080

CMD ["python", "service.py"]
