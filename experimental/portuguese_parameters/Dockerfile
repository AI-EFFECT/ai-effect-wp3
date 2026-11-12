# Existing validated FastAPI/httpx dependencies; no downloads or original-image edits.
FROM sha256:a294c9a4db5ee39b6a1c24d2d385f996e2f72ec0a0a36c8657ed77139c5ae60d
ARG SOURCE_COMMIT
LABEL org.opencontainers.image.revision=$SOURCE_COMMIT
COPY --chown=app:app experimental/portuguese_parameters /app/experimental/portuguese_parameters
COPY --chown=app:app orchestrator/src/services/parameter_contract.py /app/orchestrator/src/services/parameter_contract.py
COPY --chown=app:app orchestrator/parameter_contract_provenance.json /app/orchestrator/parameter_contract_provenance.json
ENV PYTHONPATH=/app
CMD ["python", "-m", "experimental.portuguese_parameters"]
