FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY codeagent ./codeagent

RUN useradd --create-home --uid 10001 agent
USER agent

EXPOSE 8000
CMD ["uvicorn", "codeagent.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
