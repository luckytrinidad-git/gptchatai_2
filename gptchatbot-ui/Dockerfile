FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt
RUN pip install psycopg2-binary

COPY . .

EXPOSE 8001

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8001"]
