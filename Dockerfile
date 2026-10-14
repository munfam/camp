FROM python:3.11-slim

# install system dependencies for psycopg2-binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bilingual_marker.py .

EXPOSE 8501

CMD ["streamlit", "run", "bilingual_marker.py", "--server.port=8501", "--server.address=0.0.0.0"]

