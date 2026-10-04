FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv
WORKDIR /srv/voice
COPY vendor/lili-voice-input/server/pyproject.toml vendor/lili-voice-input/server/uv.lock vendor/lili-voice-input/server/README.md ./
COPY vendor/lili-voice-input/server/src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN apt-get update && apt-get install --yes --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 lilivoice && useradd --uid 10001 --gid lilivoice --create-home lilivoice
WORKDIR /srv/voice
COPY --from=build --chown=lilivoice:lilivoice /srv/voice/.venv ./.venv
ENV PATH="/srv/voice/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER lilivoice
CMD ["uvicorn", "lili_voice_input.main:app", "--host", "0.0.0.0", "--port", "9100"]
