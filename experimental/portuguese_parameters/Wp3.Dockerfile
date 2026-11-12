# Verify retained local tag ID is edcac597f327dc817ff0c9b9c930dbf9b4cb62235d42f2ef1940845ca1820a21.
FROM ai-effect-parameter-wp3:cfc0a850548c
ARG SOURCE_COMMIT
LABEL org.opencontainers.image.revision=$SOURCE_COMMIT
# Dependencies are retained; exact reviewed maintained runtime source is additive.
COPY orchestrator/src /app/src
WORKDIR /app/src
CMD ["python", "main.py"]
