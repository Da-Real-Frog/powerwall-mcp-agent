FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Includes the README.md so uv doesn't crash during build
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

# Preserves the src directory structure
COPY src ./src

EXPOSE 8000

# Points exactly to the new src/ path!
CMD ["uv", "run", "src/server/server.py"]
