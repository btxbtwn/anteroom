# Base images are pinned by digest so a rebuild gets the same system packages.
FROM node:25-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS web
WORKDIR /src/vendoo-studio
COPY vendoo-studio/package.json vendoo-studio/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY vendoo-studio/ ./
COPY skills/list-this/references/listing-editor-fields.json /src/skills/list-this/references/listing-editor-fields.json
# The site tells the seller when Chrome runs an older Anteroom Bridge than this one.
COPY vendoo-extension/manifest.json /src/vendoo-extension/manifest.json
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_PUBLISHABLE_KEY
RUN node -e 'if (!process.env.VITE_SUPABASE_URL || !process.env.VITE_SUPABASE_PUBLISHABLE_KEY) throw new Error("Cloud public configuration is required")' \
    && ANTEROOM_HOSTED_BUILD=1 npm run build

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# The hosted packages exactly as uv.lock pins them, with their hashes.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS requirements
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY vendoo-studio/pyproject.toml vendoo-studio/uv.lock ./
RUN uv export --frozen --no-emit-project --extra hosted --format requirements-txt --hashes \
    --output-file /requirements.txt

FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANTEROOM_HOSTED=1 \
    PYTHONPATH=/app/vendoo-studio/server \
    ANTEROOM_WEB_DIR=/app/web
WORKDIR /app
COPY deploy/fly/supabase-prod-ca-2021.crt /app/supabase-prod-ca-2021.crt
COPY vendoo-studio/VERSION /app/vendoo-studio/VERSION
COPY CHANGELOG.md /app/CHANGELOG.md
COPY vendoo-studio/server/vendoo_studio/ /app/vendoo-studio/server/vendoo_studio/
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
    && chmod -R a+rX /app/skills /app/vendoo-studio /app/CHANGELOG.md \
    && useradd --create-home --uid 10001 anteroom
COPY --from=web /src/vendoo-studio/dist /app/web
ENV VENDOO_STUDIO_DATA_DIR=/home/anteroom/.local/share/anteroom
USER anteroom
EXPOSE 8080
# Authentication callbacks can contain one-use codes; do not log request URLs.
CMD ["python", "-m", "vendoo_studio.hosted_server"]
