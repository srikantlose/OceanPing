FROM python:3.12-slim

WORKDIR /srv

# CPU-only torch first so sentence-transformers doesn't pull the multi-GB CUDA wheels.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini pytest.ini ./
COPY alembic ./alembic
COPY app ./app
COPY tests ./tests
COPY training ./training

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
