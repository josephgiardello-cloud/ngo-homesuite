FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN pip install -r requirements.txt
EXPOSE 8000
CMD ["gunicorn", "-w", "3", "-k", "gthread", "-b", "0.0.0.0:8000", "ngo_homesuite.wsgi:app"]
