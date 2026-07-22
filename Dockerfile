FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY . /app/

WORKDIR /app

RUN  --mount=type=cache,target=/root/.cache/uv \
    uv sync

EXPOSE 8000

ENV PATH=/app/.venv/bin:$PATH

WORKDIR /app/src

CMD ["fastapi", "run", "--entrypoint", "server:app", "--host", "0.0.0.0", "--port", "8720"]
