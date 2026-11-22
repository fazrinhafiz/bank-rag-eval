# bankrag API image: small, non-root, no secrets baked in.
#   docker build -t bankrag .
#   docker run -p 8000:8000 -e BANKRAG_TRACE_SALT=<from your secret store> bankrag
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BANKRAG_CONFIG=/app/config.yaml

WORKDIR /app

# Dependencies first so code changes don't invalidate the install layer.
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install ".[api,docs]"

# Everything the service reads at runtime. eval/ is included only so /health reports the same
# source_fingerprint as the eval reports, which ties a running container to the results it was gated on.
COPY config.yaml ./
COPY prompts ./prompts
COPY data/docs ./data/docs
COPY rules ./rules
COPY data/docverify ./data/docverify
COPY eval/golden_set.jsonl eval/thresholds.yaml eval/docverify_thresholds.yaml ./eval/

RUN useradd --create-home --uid 10001 app && mkdir -p /app/traces && chown app /app/traces
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["uvicorn", "bankrag.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
