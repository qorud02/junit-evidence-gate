FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/qorud02/junit-evidence-gate" \
      org.opencontainers.image.description="Check JUnit testcase evidence before accepting a green CI report." \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY junit_evidence_gate/ /app/junit_evidence_gate/
COPY LICENSE /app/LICENSE
RUN python -m junit_evidence_gate --version \
    && mkdir /work

USER 10001:10001
WORKDIR /work
ENTRYPOINT ["python", "-P", "-m", "junit_evidence_gate"]
CMD ["--help"]
