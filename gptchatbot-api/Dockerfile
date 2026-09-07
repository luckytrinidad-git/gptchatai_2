FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libxcb1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install psycopg2-binary gunicorn

COPY . .

EXPOSE 8000

CMD ["gunicorn", "gptchatbot.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "180", \
     "--capture-output", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "debug"]