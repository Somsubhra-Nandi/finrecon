# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.11-slim AS application
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    FINRECON_LEDGER_PATH=/app/var/finrecon.sqlite3
WORKDIR /app

RUN groupadd --system --gid 10001 finrecon \
    && useradd --system --uid 10001 --gid finrecon --create-home finrecon \
    && mkdir -p /app/var

COPY --chown=finrecon:finrecon pyproject.toml README.md ./
COPY --chown=finrecon:finrecon src/ ./src/
RUN pip install --no-cache-dir .

# The product needs public manifests/reports, visible benchmark inputs and
# replay fixtures. Hidden ground truth is intentionally not packaged.
COPY --chown=finrecon:finrecon benchmark/manifests/ ./benchmark/manifests/
COPY --chown=finrecon:finrecon benchmark/reports/ ./benchmark/reports/
COPY --chown=finrecon:finrecon benchmark/datasets/ ./benchmark/datasets/
COPY --chown=finrecon:finrecon fixtures/demo/ ./fixtures/demo/
COPY --chown=finrecon:finrecon fixtures/trajectories/ ./fixtures/trajectories/
COPY --from=web-build --chown=finrecon:finrecon /web/dist ./web/dist
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/finrecon-entrypoint

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "from urllib.request import urlopen; assert urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200"
ENTRYPOINT ["/usr/local/bin/finrecon-entrypoint"]
CMD ["uvicorn", "finrecon.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
