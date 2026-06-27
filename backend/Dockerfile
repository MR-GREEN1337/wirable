FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy dependency manifest first for layer caching
COPY pyproject.toml .

# Install all dependencies into the system Python
RUN uv pip install --system -e .

# Copy application code
COPY . .

# Expose the API port
EXPOSE 8000

# Default: run with reload in dev; override CMD in prod with --no-reload
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
