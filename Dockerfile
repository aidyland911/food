FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py menu.json ./
COPY templates/ templates/
COPY static/ static/

ENV DB_PATH=/data/food.db
RUN mkdir /data
VOLUME /data

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "app:app"]
