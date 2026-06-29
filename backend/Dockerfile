# Backend Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY bot/ ./bot/
COPY profiles/ ./profiles/
COPY scripts/ ./scripts/
COPY run_*.py ./

# Create directories for data and reports
RUN mkdir -p data out reports/conservative reports/aggressive

# Default command runs multi-profile mode
CMD ["python", "run_api.py"]
