FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# The manifests are copied, not bound: `RUN --mount=type=bind` of a repository file is
# unreadable on an SELinux-Enforcing host (the repo carries `user_home_t`, the build
# container runs as `container_t`). A COPY layer keeps the dependency layer cached too.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev --no-editable

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.12-slim
COPY --from=builder /app/.venv /app/.venv
# The image is also the migration image: the one-shot `migrate` service in
# infra/compose.yaml runs `alembic -c /app/alembic.ini upgrade head` (docs/05).
COPY --from=builder /app/alembic.ini /app/alembic.ini
COPY --from=builder /app/migrations /app/migrations
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "techcamp.main:app", "--host", "0.0.0.0", "--port", "8000"]
