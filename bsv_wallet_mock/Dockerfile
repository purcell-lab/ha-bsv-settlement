FROM python:3.13-slim
WORKDIR /app
# Immutable, MIT-licensed service source. No moving branch is executed at build time.
ADD https://codeload.github.com/purcell-lab/ha-bsv-settlement/tar.gz/7443b7ab4f55588dd5171074efc4a3eeaa05358f /tmp/source.tar.gz
RUN mkdir /tmp/source \
    && tar -xzf /tmp/source.tar.gz -C /tmp/source --strip-components=1 \
    && cp -r /tmp/source/wallet_service /app/wallet_service \
    && cp /tmp/source/requirements.txt /app/requirements.txt \
    && cp /tmp/source/LICENSE /app/LICENSE \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && rm -rf /tmp/source /tmp/source.tar.gz
COPY run.py /app/run.py
EXPOSE 8091
CMD ["python", "/app/run.py"]
