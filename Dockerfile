FROM node:22-slim AS h5
WORKDIR /build
COPY web/h5/package.json web/h5/package-lock.json ./
RUN npm ci
COPY web/h5 ./
RUN npm run build

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv
WORKDIR /srv/island
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN groupadd --gid 10001 island && useradd --uid 10001 --gid island --create-home island
WORKDIR /srv/island
COPY --from=build --chown=island:island /srv/island/.venv ./.venv
COPY --chown=island:island app ./app
COPY --chown=island:island alembic ./alembic
COPY --chown=island:island alembic.ini ./
COPY --chown=island:island Prompt ./Prompt
COPY --from=h5 --chown=island:island /build/dist ./web/h5/dist
ENV PATH="/srv/island/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN mkdir -p /srv/island/var/imports && chown -R island:island /srv/island/var
USER island
CMD ["python", "-m", "app.server", "--host", "0.0.0.0", "--port", "8000"]
