import logging
import time
from typing import Tuple

from opentelemetry import context, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asgi import asgi_getter, asgi_setter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry import metrics as otel_metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.propagate import extract
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    PeriodicExportingMetricReader,
    ConsoleMetricExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match
from starlette.status import HTTP_500_INTERNAL_SERVER_ERROR
from starlette.types import ASGIApp


class OtelMiddleware(BaseHTTPMiddleware):
    def __init__(
        self, app: ASGIApp, app_name: str, meter_provider, trace_provider
    ) -> None:
        super().__init__(app)
        self.app_name = app_name
        self.tracer = trace_provider.get_tracer("otel-middleware")
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

        ctx = extract(request.scope, getter=asgi_getter)
        context.attach(ctx)

        with self.tracer.start_as_current_span(
            f"{method} {path}",
            context=ctx,
            kind=trace.SpanKind.SERVER,
            attributes=attributes,
        ):
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

                self.m_requests_processing_time.record(
                    after_time - before_time, attributes
                )
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
    app: ASGIApp,
    app_name: str,
    auto_instrumentation_level: str = "0",
    log_correlation: bool = True,
) -> None:
    """
    auto_instrumentation_level:
    - '0': Application instrumentation is using opentelemetry contrib for logs but custom metrics and traces
    - '1': Application instrumentation is only through the opentelemetry contrib middleware for logs, traces and metrics
    - '2': Application is expected to be executed by the opentelemetry agent `opentelemetry-instrument <my-app>`
    """
    logging.getLogger("fastapi_app").info(
        "Auto instrumentation level for service %s: %s",
        app_name,
        auto_instrumentation_level,
    )
    if auto_instrumentation_level < "2":
        # Setting OpenTelemetry
        # set the service name to show in traces
        resource = Resource.create(
            attributes={
                # Standard attribute to reference a service
                "service.name": app_name,
                # Attributes to link traces to logs
                "compose_service": app_name,
            }
        )

        # set the tracer provider
        tracer = TracerProvider(resource=resource)
        tracer.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(tracer)

        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(),
            export_interval_millis=5_000,
        )
        # Debug exporter to print metric on stdout
        # reader2 = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=5_000)
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[reader],
        )
        otel_metrics.set_meter_provider(meter_provider)

        if log_correlation:
            logging.getLogger("fastapi_app").info("Set logging instrumentation")
            LoggingInstrumentor().instrument(set_logging_format=True)

        if auto_instrumentation_level == "0":
            logging.getLogger("fastapi_app").info("Use custom otel meters.")
            app.add_middleware(
                OtelMiddleware,
                app_name=app_name,
                meter_provider=meter_provider,
                trace_provider=tracer,
            )
        elif auto_instrumentation_level == "1":
            logging.getLogger("fastapi_app").info("Set FastAPI instrumentor.")
            FastAPIInstrumentor.instrument_app(
                app,
                tracer_provider=tracer,
                meter_provider=meter_provider,
            )
