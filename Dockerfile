FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source code
COPY . .

# Expose Hugging Face default port
EXPOSE 7860

# Run FastAPI app binding to Hugging Face's port 7860
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
