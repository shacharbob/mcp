FROM python:3.11-slim

WORKDIR /app

# Install dependencies
# Copy source code
COPY inventory_mcp/ inventory_mcp/

# Install dependencies
RUN pip install --no-cache-dir --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN cat inventory_mcp/server.py

# Set defaults
ENV PORT=8080
ENV CACHE_BUST=20240115_1
EXPOSE 8080

ENTRYPOINT ["python", "-m", "inventory_mcp.server"]
