# Exact retained original ML dependencies. Operator verifies tag's full image ID.
FROM pt33-tef-integrated-synthetic-data:latest
ARG SOURCE_COMMIT
LABEL org.opencontainers.image.revision=$SOURCE_COMMIT
COPY experimental/portuguese_parameters/model_observer.py /opt/experimental/model_observer.py
ENTRYPOINT ["python", "/opt/experimental/model_observer.py"]
