FROM python:3.10

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY constraints.txt /app/constraints.txt
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir --prefer-binary -r /app/requirements.txt -c /app/constraints.txt

COPY src /app/src
COPY README.md /app/README.md

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
