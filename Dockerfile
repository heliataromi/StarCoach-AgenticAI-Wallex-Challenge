# Multi-Stage / Lightweight Production Dockerfile for Liara PaaS
FROM python:3.10-slim

WORKDIR /app

# Ensure output is directly sent to terminal without buffering
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV TRADING_MODE=shadow

# Install production dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose web dashboard / health check port
EXPOSE 8080

# Default entrypoint runs autonomous engine with web monitor
CMD ["python3", "run_engine.py", "--web"]
