# FastAPI with Opentelemetry Observability

Observe the FastAPI application with three pillars of observability on [Grafana](https://github.com/grafana/grafana):

1. Traces with [Tempo](https://github.com/grafana/tempo) and 
2. Metrics with [Prometheus](https://prometheus.io/) and [Prometheus Python Client](https://github.com/prometheus/client_python)
3. Logs with [Loki](https://github.com/grafana/loki)

using only [OpenTelemetry Python SDK](https://github.com/open-telemetry/opentelemetry-python). This is based
on the awesome demo: https://github.com/blueswen/fastapi-observability

The FastAPI application can be configured to use one of the 3 approaches
of observability with Opentelemetry using the environment variable
`OTEL_AUTO_INSTRUMENTATION_LEVEL`:

- `0`: Manual instrumentation - the code is heavily modified to handle trace and metrics
- `1`: Programmatic instrumentation - the code is lightly modified to load the instrumentation provided by the Opentelemetry Python contrib project with fine tuning of the configuration.
- `2`: Zero-code instrumentation - the code is not modified and instrumentation will be injected at the start of the python process.

This was presented at the Python Rennes Meet-up on October 10th 2024. Here is the [presentation](./Python_Rennes_20241010_OpenTelemetry.pdf) and the video.

<a href="http://www.youtube.com/watch?feature=player_embedded&v=FVLdaqFLMs0&t=3414
" target="_blank"><img src="http://img.youtube.com/vi/FVLdaqFLMs0/0.jpg" 
alt="Presentation video at Python Meet up Rennes" width="240" height="180" border="10" /></a>

> The video is in French

## Table of contents

- [FastAPI with Observability](#fastapi-with-observability)
  - [Table of contents](#table-of-contents)
  - [Quick Start](#quick-start)
  - [Explore with Grafana](#explore-with-grafana)
    - [Metrics to Traces](#metrics-to-traces)
    - [Traces to Logs](#traces-to-logs)
    - [Logs to Traces](#logs-to-traces)
  - [Detail](#detail)
    - [FastAPI Application](#fastapi-application)
      - [Traces and Logs](#traces-and-logs)
      - [Span Inject](#span-inject)
      - [Metrics](#metrics)
      - [OpenTelemetry Instrumentation](#opentelemetry-instrumentation)
    - [Prometheus - Metrics](#prometheus---metrics)
      - [Prometheus Config](#prometheus-config)
      - [Grafana Data Source](#grafana-data-source)
    - [Tempo - Traces](#tempo---traces)
      - [Grafana Data Source](#grafana-data-source-1)
    - [Loki - Logs](#loki---logs)
      - [Loki Docker Driver](#loki-docker-driver)
      - [Grafana Data Source](#grafana-data-source-2)
    - [Grafana](#grafana)
  - [Reference](#reference)

## Quick Start

1. Install [Loki Docker Driver](https://grafana.com/docs/loki/latest/clients/docker-driver/)

   ```bash
   docker plugin install grafana/loki-docker-driver:2.9.2 --alias loki --grant-all-permissions
   ```

2. Start all services with docker-compose

   ```bash
   docker-compose up -d
   ```

   If got the error message `Error response from daemon: error looking up logging plugin loki: plugin loki found but disabled`, please run the following command to enable the plugin:

   ```bash
   docker plugin enable loki
   ```

3. Send requests with [siege](https://linux.die.net/man/1/siege) and curl to the FastAPI app

   ```bash
   bash request-script.sh
   bash trace.sh
   ```

   Or you can use [Locust](https://locust.io/) to send requests:

   ```bash
   # install locust first with `pip install locust` if you don't have it
   locust -f locustfile.py --headless --users 10 --spawn-rate 1 -H http://localhost:8000
   ```

   Or you can send requests with [k6](https://k6.io/):

   ```bash
   k6 run --vus 1 --duration 300s k6-script.js
   ```

4. Check predefined dashboard `FastAPI Observability` on Grafana [http://localhost:3000/](http://localhost:3000/) login with `admin:admin`

   Dashboard screenshot:

   ![FastAPI Monitoring Dashboard](./images/dashboard.png)

   The dashboard is also available on [Grafana Dashboards](https://grafana.com/grafana/dashboards/16110).


> [!NOTE]
> This quick start present the fully manual instrumentation (level `0`).
> For level `1` and `2`, you will have to use the `FastAPI Otel Observability` dashboard
> as the meters will have a different name.


## Explore with Grafana

Grafana provides a great solution, which could observe specific actions in service between traces, metrics, and logs through trace ID and exemplar.

![Observability Correlations](./images/observability-correlations.jpeg)

Image Source: [Grafana](https://grafana.com/blog/2021/03/31/intro-to-exemplars-which-enable-grafana-tempos-distributed-tracing-at-massive-scale/)

### Metrics to Traces

Get Trace ID from an exemplar in metrics, then query in Tempo.

Query: `histogram_quantile(.99,sum(rate(fastapi_requests_duration_seconds_bucket{app_name="app-a", path!="/metrics"}[1m])) by(path, le))`

![Metrics to Traces](./images/metrics-to-traces.png)

### Traces to Logs

Get Trace ID and tags (here is `compose.service`) defined in Tempo data source from span, then query with Loki.

![Traces to Logs](./images/traces-to-logs.png)

### Logs to Traces

Get Trace ID from log (regex defined in Loki data source), then query in Tempo.

![Logs to Traces](./images/logs-to-traces.png)

## Detail

### FastAPI Application

For a more complex scenario, we use three FastAPI applications with the same code in this demo. There is a cross-service action in `/chain` endpoint, which provides a good example of how to use OpenTelemetry SDK and how Grafana presents trace information.

#### Traces and Logs

We use [OpenTelemetry Python SDK](https://github.com/open-telemetry/opentelemetry-python) to send trace info with http to Tempo. Each request span contains other child spans when using OpenTelemetry instrumentation. The reason is that instrumentation will catch each internal asgi interaction ([opentelemetry-python-contrib issue #831](https://github.com/open-telemetry/opentelemetry-python-contrib/issues/831#issuecomment-1005163018)). If you want to get rid of the internal spans, there is a [workaround](https://github.com/open-telemetry/opentelemetry-python-contrib/issues/831#issuecomment-1116225314) in the same issue #831 by using a new OpenTelemetry middleware with two overridden methods for span processing.

We use [OpenTelemetry Logging Instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/logging/logging.html) to override the logger format with another format with trace id and span id.

The following image shows the span info sent to Tempo and queried on Grafana. Trace span info provided by `FastAPIInstrumentor` with trace ID (17785b4c3d530b832fb28ede767c672c), span id(d410eb45cc61f442), service name(app-a), custom attributes(service.name=app-a, compose_service=app-a) and so on.

![Span Information](./images/span-info.png)

Log format with trace id and span id, which is overridden by `LoggingInstrumentor``

```txt
%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s resource.service.name=%(otelServiceName)s] - %(message)s
```

The following image is what the logs look like.

![Log With Trace ID And Span ID](./images/log-format.png)

#### Span Inject

If you want other services to use the same Trace ID, you have to use `inject` function to add current span information to the header. Because OpenTelemetry FastAPI instrumentation only takes care of the asgi app's request and response, it does not affect any other modules or actions like sending HTTP requests to other servers or function calls.

Alternatively, we can use the [instrumentation library for HTTPX](https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/instrumentation/opentelemetry-instrumentation-httpx) to instrument HTTPX. Following is the example of using OpenTelemetry HTTPX Instrumentation which will automatically inject trace info to the header.

```py
import httpx
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

HTTPXClientInstrumentor().instrument()

@app.get("/chain")
async def chain(response: Response):
    async with httpx.AsyncClient() as client:
        await client.get(f"http://localhost:8000/")
    async with httpx.AsyncClient() as client:
        await client.get(f"http://{TARGET_ONE_HOST}:8000/io_task")
    async with httpx.AsyncClient() as client:
        await client.get(f"http://{TARGET_TWO_HOST}:8000/cpu_task")

    return {"path": "/chain"}
```

> This is what will happen in the zero-code instrumentation case.

#### Metrics

We use the OpenTelemetry Python SDK to generate metrics with exemplars.

> Exemplars are not yet available in the stable version of the Python SDK (as of 1.27.0). But they
> will be in the next release.

The metrics are pushed directly to Prometheus as it is configured
through a feature flag to support receiving OpenTelemetry metrics (`--enable-feature=otlp-write-receiver`). This induces some constraints
on Prometheus capabilities.

> To still use Prometheus in pull mode when using OpenTelemetry, a good
> solution is to add a OpenTelemetry to which your services will pushed
> metrics and from which Prometheus will pull them.


#### OpenTelemetry Instrumentation

There are two methods to add trace information to spans and logs using the OpenTelemetry Python SDK:

1. [Code-based Instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/): This involves adding trace information to spans, logs, and metrics using the OpenTelemetry Python SDK. It requires more coding effort but allows for the addition of exemplars to metrics. We employ this approach in this project.
2. [Zero-code Instrumentation](https://opentelemetry.io/docs/zero-code/python/): This method automatically instruments a Python application using instrumentation libraries, but only when the used [frameworks and libraries](https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/instrumentation#readme) are supported. It simplifies the process by eliminating the need for manual code changes. For more insights into zero-code instrumentation, refer to my other project, [OpenTelemetry APM](https://github.com/blueswen/opentelemetry-apm?tab=readme-ov-file#python---fastapi).

### Prometheus - Metrics

Collects metrics from applications.

#### Grafana Data Source

Add an Exemplars which uses the value of `trace_id` label to create a Tempo link.

Grafana data source setting example:

![Data Source of Prometheus: Exemplars](./images/prometheus-exemplars.png)

Grafana data sources config example:

```yaml
name: Prometheus
type: prometheus
typeName: Prometheus
access: proxy
url: http://prometheus:9090
password: ''
user: ''
database: ''
basicAuth: false
isDefault: true
jsonData:
exemplarTraceIdDestinations:
   - datasourceUid: tempo
      name: trace_id
httpMethod: POST
readOnly: false
editable: true
```

### Tempo - Traces

Receives spans from applications.

#### Grafana Data Source

[Trace to logs](https://grafana.com/docs/grafana/latest/datasources/tempo/#trace-to-logs) setting:

1. Data source: target log source
2. Tags: key of tags or process level attributes from the trace, which will be log query criteria if the key exists in the trace
3. Map tag names: Convert existing key of tags or process level attributes from trace to another key, then used as log query criteria. Use this feature when the values of the trace tag and log label are identical but the keys are different.

Grafana data source setting example:

![Data Source of Tempo: Trace to logs](./images/tempo-trace-to-logs.png)

Grafana data sources config example:

```yaml
name: Tempo
type: tempo
typeName: Tempo
access: proxy
url: http://tempo
password: ''
user: ''
database: ''
basicAuth: false
isDefault: false
jsonData:
nodeGraph:
   enabled: true
tracesToLogs:
   datasourceUid: loki
   filterBySpanID: false
   filterByTraceID: true
   mapTagNamesEnabled: false
   tags:
      - compose_service
readOnly: false
editable: true
```

### Loki - Logs

Collect logs with Loki Docker Driver from all services.

#### Loki Docker Driver

1. Use [YAML anchor and alias](https://support.atlassian.com/bitbucket-cloud/docs/yaml-anchors/) feature to set logging options for each service.
2. Set [Loki Docker Driver options](https://grafana.com/docs/loki/latest/clients/docker-driver/configuration/)
   1. loki-url: loki service endpoint
   2. loki-pipeline-stages: processes multiline log from FastAPI application with multiline and regex stages ([reference](https://grafana.com/docs/loki/latest/clients/promtail/stages/multiline/))

```yaml
x-logging: &default-logging # anchor(&): 'default-logging' for defines a chunk of configuration
  driver: loki
  options:
    loki-url: 'http://localhost:3100/api/prom/push'
    loki-pipeline-stages: |
      - multiline:
          firstline: '^\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}:\d{2}'
          max_wait_time: 3s
      - regex:
          expression: '^(?P<time>\d{4}-\d{2}-\d{2} \d{1,2}:\d{2}:\d{2},d{3}) (?P<message>(?s:.*))$$'
# Use $$ (double-dollar sign) when your configuration needs a literal dollar sign.

version: "3.4"

services:
   foo:
      image: foo
      logging: *default-logging # alias(*): refer to 'default-logging' chunk 
```

#### Grafana Data Source

Add a TraceID derived field to extract the trace id and create a Tempo link from the trace id.

Grafana data source setting example:

![Data Source of Loki: Derived fields](./images/loki-derive-filed.png)

Grafana data source config example:

```yaml
name: Loki
type: loki
typeName: Loki
access: proxy
url: http://loki:3100
password: ''
user: ''
database: ''
basicAuth: false
isDefault: false
jsonData:
derivedFields:
   - datasourceUid: tempo
      matcherRegex: (?:trace_id)=(\w+)
      name: TraceID
      url: $${__value.raw}
      # Use $$ (double-dollar sign) when your configuration needs a literal dollar sign.
readOnly: false
editable: true
```

### Grafana

1. Add Prometheus, Tempo, and Loki to the data source with config file `etc/grafana/datasource.yml`.
2. Load predefined dashboard with `etc/dashboards.yaml` and `etc/dashboards/fastapi-observability.json`.

```yaml
# grafana in docker-compose.yaml
grafana:
   image: grafana/grafana:10.4.2
   volumes:
      - ./etc/grafana/:/etc/grafana/provisioning/datasources # data sources
      - ./etc/dashboards.yaml:/etc/grafana/provisioning/dashboards/dashboards.yaml # dashboard setting
      - ./etc/dashboards:/etc/grafana/dashboards # dashboard json files directory
```
