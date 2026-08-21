FROM python:3.12-slim
WORKDIR /app

# Install composeio library first (local package, not on PyPI)
COPY composeio_lib/ /tmp/composeio_lib/
RUN pip install --no-cache-dir /tmp/composeio_lib/ && rm -rf /tmp/composeio_lib/

# Install MCP server dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy MCP server code
COPY intelligence_mcp/ ./intelligence_mcp/

EXPOSE 8433
CMD ["python", "-m", "intelligence_mcp", "--host", "0.0.0.0"]
