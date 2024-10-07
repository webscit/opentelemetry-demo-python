FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get -y upgrade \
    && apt-get -y install --no-install-recommends git \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements.txt
COPY requirements-dev.txt requirements-dev.txt

RUN pip install -r requirements.txt
# RUN pip install -r requirements-dev.txt
RUN git clone --depth=20 --single-branch --branch=main https://github.com/open-telemetry/opentelemetry-python.git core \
    && cd core \
    && git checkout f15821fe272707930caab57c0e906bdb0d04074f \
    && pip install ./opentelemetry-api \
        ./opentelemetry-sdk \
        ./opentelemetry-semantic-conventions \
        ./opentelemetry-proto \
        ./exporter/opentelemetry-exporter-otlp \
        ./exporter/opentelemetry-exporter-otlp-proto-common \
        ./exporter/opentelemetry-exporter-otlp-proto-http \
        ./exporter/opentelemetry-exporter-otlp-proto-grpc \
    && cd ..

RUN git clone --depth=40 --single-branch --branch=main https://github.com/open-telemetry/opentelemetry-python-contrib.git contrib\
    && cd contrib \
    && git checkout ee67ea8ba5380e55be1a9bb1f7e8bc916ff3bdec \
    && pip install ./opentelemetry-instrumentation \
        ./opentelemetry-distro \
        ./util/opentelemetry-util-http \
        ./instrumentation/opentelemetry-instrumentation-asgi \
        ./instrumentation/opentelemetry-instrumentation-httpx \
        ./instrumentation/opentelemetry-instrumentation-fastapi \
        ./instrumentation/opentelemetry-instrumentation-logging \
    && cd ..

COPY . .

EXPOSE 8000

CMD ["python", "main.py"]
