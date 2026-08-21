FROM python:3.12-slim

WORKDIR /app

# Copy application (zero external dependencies - stdlib only)
COPY server.py /app/server.py

# Create persistent directories
RUN mkdir -p /app/data /app/data/blobs

EXPOSE 8421

ENV COMPOSEIO_DOCSTORE_DB=/app/data/docstore.db
ENV COMPOSEIO_DOCSTORE_PORT=8421

CMD ["python3", "server.py"]
