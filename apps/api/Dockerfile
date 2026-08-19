# The API image, used by Render for the public demo deployment.
#
# Two stages so the runtime image carries no build tooling: the first resolves and installs the
# locked dependencies, the second copies the finished virtualenv on top of a clean base.
#
# uv rather than pip, matching local development — the same uv.lock produces the same tree here as
# it does on a laptop, which is the whole point of committing a lock file.

# ------------------------------------------------------------------------------- Build stage
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

# Compile to bytecode at build time rather than on first request, and copy packages in rather
# than symlinking them, so the venv survives being moved to the runtime stage.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencies first, in their own layer. They change far less often than application code, so a
# code-only edit reuses this layer instead of reinstalling the world.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY . .

# Now install the project itself.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ----------------------------------------------------------------------------- Runtime stage
FROM python:3.12-slim-bookworm

# Run as a non-root user. Nothing here needs root, and a container that does not need it should
# not have it.
RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

COPY --from=build --chown=appuser:appuser /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8000

# The entrypoint applies migrations, then execs uvicorn. It reads $PORT at runtime because
# Render supplies it and it is not always 8000.
CMD ["./docker-entrypoint.sh"]
