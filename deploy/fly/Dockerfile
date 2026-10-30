FROM node:22-slim AS web
WORKDIR /src/vendoo-studio
COPY vendoo-studio/package.json vendoo-studio/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY vendoo-studio/ ./
COPY skills/list-this/references/listing-editor-fields.json /src/skills/list-this/references/listing-editor-fields.json
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_PUBLISHABLE_KEY
RUN node -e 'if (!process.env.VITE_SUPABASE_URL || !process.env.VITE_SUPABASE_PUBLISHABLE_KEY) throw new Error("Cloud public configuration is required")' \
    && ANTEROOM_HOSTED_BUILD=1 npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANTEROOM_HOSTED=1 \
    PYTHONPATH=/app/vendoo-studio/server \
    ANTEROOM_WEB_DIR=/app/web
WORKDIR /app
COPY deploy/fly/supabase-prod-ca-2021.crt /app/supabase-prod-ca-2021.crt
COPY vendoo-studio/pyproject.toml vendoo-studio/VERSION /app/vendoo-studio/
COPY CHANGELOG.md /app/CHANGELOG.md
COPY vendoo-studio/server/vendoo_studio/ /app/vendoo-studio/server/vendoo_studio/
COPY skills/list-this/SKILL.md /app/skills/list-this/SKILL.md
COPY skills/list-this/references/ /app/skills/list-this/references/
RUN pip install --no-cache-dir '/app/vendoo-studio[hosted]' \
    && useradd --create-home --uid 10001 anteroom
COPY --from=web /src/vendoo-studio/dist /app/web
ENV VENDOO_STUDIO_DATA_DIR=/home/anteroom/.local/share/anteroom
USER anteroom
EXPOSE 8080
# Authentication callbacks can contain one-use codes; do not log request URLs.
CMD ["python", "-m", "uvicorn", "vendoo_studio.hosted:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
