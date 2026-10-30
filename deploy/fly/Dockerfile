FROM node:22-slim AS web
WORKDIR /src
COPY vendoo-studio/package.json vendoo-studio/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY vendoo-studio/ ./
ARG VITE_SUPABASE_URL
ARG VITE_SUPABASE_PUBLISHABLE_KEY
RUN test -n "$VITE_SUPABASE_URL" && test -n "$VITE_SUPABASE_PUBLISHABLE_KEY" \
    && ANTEROOM_HOSTED_BUILD=1 npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/vendoo-studio/server \
    ANTEROOM_WEB_DIR=/app/web
WORKDIR /app
COPY deploy/fly/supabase-prod-ca-2021.crt /app/supabase-prod-ca-2021.crt
COPY vendoo-studio/pyproject.toml vendoo-studio/VERSION /app/vendoo-studio/
COPY vendoo-studio/server/vendoo_studio/ /app/vendoo-studio/server/vendoo_studio/
RUN pip install --no-cache-dir '/app/vendoo-studio[hosted]' \
    && useradd --create-home --uid 10001 anteroom
COPY --from=web /src/dist /app/web
ENV VENDOO_STUDIO_DATA_DIR=/home/anteroom/.local/share/anteroom
USER anteroom
EXPOSE 8080
# Authentication callbacks can contain one-use codes; do not log request URLs.
CMD ["python", "-m", "uvicorn", "vendoo_studio.hosted:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
