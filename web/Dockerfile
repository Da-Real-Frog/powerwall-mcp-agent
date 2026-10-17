FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv

WORKDIR /app

# Dependencies first, so code changes don't reinstall them
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

COPY app.py ./
COPY static ./static

EXPOSE 8080

CMD ["uv", "run", "--no-sync", "app.py"]
