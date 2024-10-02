import logging
import time
from typing import Tuple

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry import metrics as otel_metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    PeriodicExportingMetricReader,
    ConsoleMetricExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from prometheus_client.openmetrics.exposition import (
    CONTENT_TYPE_LATEST,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR
from starlette.types import ASGIApp

INFO = Gauge("fastapi_app_info", "FastAPI application information.", ["app_name"])
REQUESTS = Counter(
    "fastapi_requests_total",
    "Total count of requests by method and path.",
    ["method", "path", "app_name"],
)
RESPONSES = Counter(
    "fastapi_responses_total",
    "Total count of responses by method, path and status codes.",
    ["method", "path", "status_code", "app_name"],
)
REQUESTS_PROCESSING_TIME = Histogram(
    "fastapi_requests_duration_seconds",
    "Histogram of requests processing time by path (in seconds)",
    ["method", "path", "app_name"],
)
EXCEPTIONS = Counter(
    "fastapi_exceptions_total",
    "Total count of exceptions raised by path and exception type",
    ["method", "path", "exception_type", "app_name"],
)
REQUESTS_IN_PROGRESS = Gauge(
    "fastapi_requests_in_progress",
    "Gauge of requests by method and path currently being processed",
    ["method", "path", "app_name"],
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, app_name: str = "fastapi-app") -> None:
        super().__init__(app)
        self.app_name = app_name
        INFO.labels(app_name=self.app_name).inc()

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        method = request.method
        path, is_handled_path = self.get_path(request)

        if not is_handled_path:
            return await call_next(request)

        REQUESTS_IN_PROGRESS.labels(
            method=method, path=path, app_name=self.app_name
        ).inc()
        REQUESTS.labels(method=method, path=path, app_name=self.app_name).inc()
        before_time = time.perf_counter()
        try:
            response = await call_next(request)
        except BaseException as e:
            status_code = HTTP_500_INTERNAL_SERVER_ERROR
            EXCEPTIONS.labels(
                method=method,
                path=path,
                exception_type=type(e).__name__,
                app_name=self.app_name,
            ).inc()
            raise e from None
        else:
            status_code = response.status_code
            after_time = time.perf_counter()
            # retrieve trace id for exemplar
            span = trace.get_current_span()
            trace_id = trace.format_trace_id(span.get_span_context().trace_id)

            REQUESTS_PROCESSING_TIME.labels(
                method=method, path=path, app_name=self.app_name
            ).observe(after_time - before_time, exemplar={"TraceID": trace_id})
        finally:
            RESPONSES.labels(
                method=method,
                path=path,
                status_code=status_code,
                app_name=self.app_name,
            ).inc()
            REQUESTS_IN_PROGRESS.labels(
                method=method, path=path, app_name=self.app_name
            ).dec()

        return response

    @staticmethod
    def get_path(request: Request) -> Tuple[str, bool]:
        for route in request.app.routes:
            match, child_scope = route.matches(request.scope)
            if match == Match.FULL:
                return route.path, True

        return request.url.path, False


def metrics(request: Request) -> Response:
    return Response(
        generate_latest(REGISTRY), headers={"Content-Type": CONTENT_TYPE_LATEST}
    )


class OtelMiddleware(BaseHTTPMiddleware):
    def __init__(
        self, app: ASGIApp, app_name: str = "fastapi-app", meter_provider=None
    ) -> None:
        super().__init__(app)
        self.app_name = app_name

        meter = meter_provider.get_meter("otel-middleware", version="1.0.0")

        self.m_requests = meter.create_counter(
            "fastapi_requests_total",
            description="Total count of requests by method and path.",
        )
        self.m_responses = meter.create_counter(
            "fastapi_responses_total",
            description="Total count of responses by method, path and status codes.",
        )
        self.m_requests_processing_time = meter.create_histogram(
            "fastapi_requests_duration_seconds",
            unit="s",
            description="Histogram of requests processing time by path (in seconds)",
        )
        self.m_exceptions = meter.create_counter(
            "fastapi_exceptions_total",
            description="Total count of exceptions raised by path and exception type",
        )
        self.m_requests_in_progress = meter.create_up_down_counter(
            "fastapi_requests_in_progress",
            description="Gauge of requests by method and path currently being processed",
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        method = request.method
        path, is_handled_path = self.get_path(request)
        if not is_handled_path:
            return await call_next(request)

        attributes = {"method": method, "path": path, "app_name": self.app_name}

        self.m_requests_in_progress.add(1, attributes)

        self.m_requests.add(1, attributes)

        before_time = time.perf_counter()
        try:
            response = await call_next(request)
        except BaseException as e:
            status_code = HTTP_500_INTERNAL_SERVER_ERROR
            exception_attributes = attributes.copy()
            exception_attributes.update({"exception_type": type(e).__name__})
            self.m_exceptions.add(1, exception_attributes)
            raise e from None
        else:
            status_code = response.status_code
            after_time = time.perf_counter()

            self.m_requests_processing_time.record(after_time - before_time, attributes)

            # FIXME - done automatically in otel - retrieve trace id for exemplar
            # span = trace.get_current_span()
            # trace_id = trace.format_trace_id(span.get_span_context().trace_id)

            # REQUESTS_PROCESSING_TIME.labels(
            #     method=method, path=path, app_name=self.app_name
            # ).observe(, exemplar={"TraceID": trace_id})
        finally:
            response_attributes = attributes.copy()
            response_attributes.update({"status_code": status_code})
            self.m_responses.add(1, response_attributes)
            self.m_requests_in_progress.add(-1, attributes)

        return response

    @staticmethod
    def get_path(request: Request) -> Tuple[str, bool]:
        for route in request.app.routes:
            match, child_scope = route.matches(request.scope)
            if match == Match.FULL:
                return route.path, True

        return request.url.path, False


def setting_otlp(
    app: ASGIApp, app_name: str, endpoint: str, log_correlation: bool = True
) -> None:
    # Setting OpenTelemetry
    # set the service name to show in traces
    resource = Resource.create(
        attributes={"service.name": app_name, "compose_service": app_name}
    )

    # set the tracer provider
    tracer = TracerProvider(resource=resource)
    tracer.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(tracer)

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(),
        export_interval_millis=5_000,
    )
    # Debug exporter to print metric on stdout
    # reader2 = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=5_000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    otel_metrics.set_meter_provider(meter_provider)

    if log_correlation:
        LoggingInstrumentor().instrument(set_logging_format=True)

    app.add_middleware(OtelMiddleware, app_name=app_name, meter_provider=meter_provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=tracer)
