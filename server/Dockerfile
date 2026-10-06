FROM python:3.12-slim

# Install uv directly from Astral
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Install dependencies safely
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

# Copy application code
COPY src/ ./src

EXPOSE 8000
CMD ["uv", "run", "server/server.py"]
