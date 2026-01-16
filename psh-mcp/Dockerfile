# Use official lightweight Python image
FROM python:3.11-slim

# Prevent python from writing pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY inspect_fastmcp.py .
COPY psh_mcp ./psh_mcp
COPY tests ./tests

# Default configuration (safe mode)
ENV PORT=8080
ENV CACHE_BUST=20240115_1
ENV MCP_MODE=auditor
ENV PYTHONPATH=/app

# Run the server
CMD ["python", "psh_mcp/server.py"]
