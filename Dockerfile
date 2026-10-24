FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY db db
# confere rotas e telas: se o pacote estiver incompleto, o build para e a versão anterior continua no ar
RUN python -m app.verificar
# Cloud Run define $PORT
CMD exec gunicorn --bind :${PORT:-8080} --workers 2 --threads 4 --timeout 60 "app:create_app()"
