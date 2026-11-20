# Base images are pinned by digest so a rebuild gets the same system packages.
# Python stays on the minor version CI tests and basedpyright targets (3.12);
# moving it is a deliberate change to all three (tests/test_hosted_image_runtime.py).
FROM node:25-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS web
WORKDIR /src/anteroom
COPY anteroom/package.json anteroom/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY anteroom/ ./
COPY skills/list-this/references/listing-editor-fields.json /src/skills/list-this/references/listing-editor-fields.json
# The site tells the seller when Chrome runs an older Anteroom Bridge than this one.
COPY anteroom-extension/manifest.json /src/anteroom-extension/manifest.json
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_PUBLISHABLE_KEY
RUN node -e 'if (!process.env.VITE_SUPABASE_URL || !process.env.VITE_SUPABASE_PUBLISHABLE_KEY) throw new Error("Cloud public configuration is required")' \
    && ANTEROOM_HOSTED_BUILD=1 npm run build

FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv

# The hosted packages exactly as uv.lock pins them, with their hashes.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS requirements
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY anteroom/pyproject.toml anteroom/uv.lock ./
RUN uv export --frozen --no-emit-project --extra hosted --format requirements-txt --hashes \
    --output-file /requirements.txt

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANTEROOM_HOSTED=1 \
    PYTHONPATH=/app/anteroom/server \
    ANTEROOM_WEB_DIR=/app/web
WORKDIR /app
COPY deploy/fly/supabase-prod-ca-2021.crt /app/supabase-prod-ca-2021.crt
COPY anteroom/VERSION /app/anteroom/VERSION
COPY CHANGELOG.md /app/CHANGELOG.md
COPY anteroom/server/anteroom/ /app/anteroom/server/anteroom/
COPY skills/list-this/SKILL.md /app/skills/list-this/SKILL.md
COPY skills/list-this/references/ /app/skills/list-this/references/
# Bulk listing reads measurements and Sourcing runs Box Scout from these skills' scripts.
COPY skills/measure-this/ /app/skills/measure-this/
COPY skills/box-scout/ /app/skills/box-scout/
COPY --from=requirements /requirements.txt /tmp/requirements.txt
# The app itself is not pip-installed: PYTHONPATH serves it from its source
# folder, and installing it would build it with an unpinned setuptools.
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && chmod -R a+rX /app/skills /app/anteroom /app/CHANGELOG.md \
    && useradd --create-home --uid 10001 anteroom
COPY --from=web /src/anteroom/dist /app/web
ENV ANTEROOM_DATA_DIR=/home/anteroom/.local/share/anteroom
USER anteroom
EXPOSE 8080
# Authentication callbacks can contain one-use codes; do not log request URLs.
CMD ["python", "-m", "anteroom.hosted_server"]
