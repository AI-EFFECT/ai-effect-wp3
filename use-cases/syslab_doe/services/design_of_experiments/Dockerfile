FROM python:3.11-slim

WORKDIR /app

COPY syslab_doe/services/design_of_experiments/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ ./common/
COPY syslab_doe/services/design_of_experiments/doe.py .
COPY syslab_doe/services/design_of_experiments/service.py .
COPY syslab_doe/requests/ ./requests/

EXPOSE 8080

CMD ["python", "service.py"]
