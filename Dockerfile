FROM python:3.14-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MATCH_DATA=/data COOKIE_SECURE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --system --uid 1001 app && mkdir -p /data && chown app /data
USER app
EXPOSE 8000
# One worker with threads: jobs run in threads and the rate limits live in memory (docs/DEPLOYMENT.md).
CMD ["gunicorn", "-c", "gunicorn.conf.py", "-b", "0.0.0.0:8000", "app:create_app()"]
