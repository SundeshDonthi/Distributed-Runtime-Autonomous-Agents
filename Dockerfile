# Build native code in a disposable stage; ship only its shared library.
FROM python:3.12-slim AS build
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends g++ && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY native/ native/
COPY setup.py .
RUN mkdir -p runtime/routing && python setup.py build_ext --inplace

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home runtime
COPY runtime/ runtime/
COPY mcp_servers/ mcp_servers/
COPY scripts/ scripts/
COPY benchmarks/results/ benchmarks/results/
COPY --from=build /app/runtime/routing/_native*.so runtime/routing/
RUN mkdir -p /app/data && chown runtime:runtime /app/data
USER runtime
ENV PYTHONUNBUFFERED=1
CMD ["sh", "-c", "exec uvicorn runtime.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
