FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv

WORKDIR /app

# Dependencies first, so code changes don't reinstall them
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the project itself and its site layout
COPY src ./src
COPY site.toml ./
RUN uv sync --frozen --no-dev

# pypowerwall keeps its login cache here; mount a volume on /data to keep it across restarts
ENV PW_CACHE_DIR=/data \
    SITE_CONFIG=/app/site.toml
VOLUME /data

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "server"]
