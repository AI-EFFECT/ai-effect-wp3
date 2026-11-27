FROM python:3.11-slim

WORKDIR /app

COPY syslab_doe/services/kpi_calculator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ ./common/
COPY syslab_doe/services/kpi_calculator/kpi.py .
COPY syslab_doe/services/kpi_calculator/kpi_catalogue.yaml .
COPY syslab_doe/services/kpi_calculator/service.py .

EXPOSE 8080

CMD ["python", "service.py"]
