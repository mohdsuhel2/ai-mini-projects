FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt groww_gateway/requirements-gateway.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-gateway.txt

COPY groww_client.py groww_gateway_transport.py ./
COPY groww_gateway ./groww_gateway

ENV PYTHONPATH=/app
EXPOSE 8787

CMD ["uvicorn", "groww_gateway.app:app", "--host", "0.0.0.0", "--port", "8787"]
