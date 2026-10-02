FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY wallet_service wallet_service
RUN useradd --uid 10001 --create-home demo && mkdir /data && chown demo:demo /data
USER demo
ENV MOCK_DB=/data/wallet.sqlite3
EXPOSE 8091
CMD ["uvicorn", "wallet_service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8091"]
