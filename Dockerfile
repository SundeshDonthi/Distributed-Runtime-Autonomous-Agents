# One image, three roles (Section 15J Phase 8: API, scheduler, and workers as
# genuinely separate OS processes -- not three code paths inside one process).
# `docker-compose.yml` runs this same image as the `api`, `scheduler`, and
# `worker` services, overriding only `command:` per service.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY runtime/ runtime/
COPY scripts/ scripts/

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "runtime.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
