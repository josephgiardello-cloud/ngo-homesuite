FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system --gid 10001 appgroup \
	&& adduser --system --uid 10001 --ingroup appgroup appuser

COPY requirements.txt requirements-core.txt requirements-db.txt requirements-ai.txt requirements-cloud.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

RUN mkdir -p /app/data /app/logs /app/backups \
	&& chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
	CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read()" || exit 1

CMD ["gunicorn", "-w", "3", "-k", "gthread", "--threads", "4", "--timeout", "60", "--graceful-timeout", "20", "--max-requests", "1000", "--max-requests-jitter", "100", "-b", "0.0.0.0:8000", "ngo_homesuite.wsgi:app"]
