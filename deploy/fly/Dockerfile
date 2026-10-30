FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/vendoo-studio/server
WORKDIR /app
COPY vendoo-studio/pyproject.toml vendoo-studio/VERSION /app/vendoo-studio/
COPY vendoo-studio/server/vendoo_studio/ /app/vendoo-studio/server/vendoo_studio/
RUN pip install --no-cache-dir '/app/vendoo-studio[hosted]' \
    && useradd --create-home --uid 10001 anteroom
USER anteroom
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "vendoo_studio.hosted:app", "--host", "0.0.0.0", "--port", "8080"]
