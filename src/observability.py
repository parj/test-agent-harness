"""
OpenTelemetry wiring — traces + metrics + logs exported over OTLP/HTTP to
the SigNoz collector (docker-compose brings it up on :4318).

Two separate providers/resources are set up:
  - "finagent-api"  — the FastAPI backend: HTTP spans (auto), LLM/tool/task
    spans (manual, see agent/runtime.py and server.py), token + cache +
    task-duration metrics.
  - "finagent-web"  — browser RUM relayed through POST /api/rum (clicks,
    page-load timing, per-view duration). Kept as a separate service so
    SigNoz's service list and the Web Vitals dashboard see "the website"
    as its own thing rather than folded into the API's traffic.

Call setup_telemetry(app) once at server startup. Everything degrades to a
no-op if OTEL_ENABLED=false or the collector is unreachable — the OTLP
exporters retry/drop in the background rather than raising into request
handlers.
"""
from __future__ import annotations

import logging
import time

from opentelemetry import _logs as otel_logs
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from config import settings

tracer = trace.get_tracer("finagent.api")
rum_tracer = trace.get_tracer("finagent.web")
meter = metrics.get_meter("finagent.api")
rum_meter = metrics.get_meter("finagent.web")

# Populated by setup_telemetry(); safe to import and call before that (no-ops).
task_counter = None
task_duration_ms = None
llm_tokens_counter = None
llm_call_duration_ms = None
tool_duration_ms = None
cache_result_counter = None

rum_click_counter = None
rum_page_timing_ms = None
rum_page_view_duration_ms = None

# Core Web Vitals — named bare (no "finagent." prefix) and matching units to
# line up with SigNoz's stock "Web Vitals Monitoring" dashboard template,
# which queries these exact metric names (lcp/inp/ttfb/fcp as ms histograms,
# cls as a unitless gauge) filtered by service.name.
_web_vital_histograms = {}
_cls_gauge = None

_started = False


def _build_provider(service_name: str, endpoint: str):
    resource = Resource.create({SERVICE_NAME: service_name})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    )

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics"),
        export_interval_millis=10_000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    return tracer_provider, meter_provider


def setup_telemetry(app) -> None:
    """Wires up OTel for the FastAPI app. Safe to call multiple times
    (only does work once); safe to call with the collector down."""
    global _started, rum_tracer, rum_meter
    global task_counter, task_duration_ms, llm_tokens_counter, llm_call_duration_ms
    global tool_duration_ms, cache_result_counter
    global rum_click_counter, rum_page_timing_ms, rum_page_view_duration_ms

    if _started or not settings.otel_enabled:
        return
    _started = True

    api_tracer_provider, api_meter_provider = _build_provider(
        settings.otel_service_name, settings.otel_exporter_endpoint
    )
    web_tracer_provider, web_meter_provider = _build_provider(
        settings.otel_rum_service_name, settings.otel_exporter_endpoint
    )

    # `tracer`/`meter` (module-level, module import time) are OTel API proxy
    # objects that resolve to whatever provider is "current" at call time —
    # setting these as the global default providers is enough to make every
    # earlier `from observability import tracer` binding start emitting
    # through them, no reassignment needed. The RUM providers are never set
    # as the global default (only the API service should own that), so
    # rum_tracer/rum_meter are bound directly to their own provider instances.
    trace.set_tracer_provider(api_tracer_provider)
    metrics.set_meter_provider(api_meter_provider)

    rum_tracer = web_tracer_provider.get_tracer("finagent.web")
    rum_meter = web_meter_provider.get_meter("finagent.web")

    # Logs: only the API service's own process logs are meaningful to ship
    # (RUM has no Python-side log stream of its own). Attaching the handler
    # to the root logger picks up both uvicorn's own logging.* calls and
    # any logging.getLogger(__name__) call anywhere in the app, without
    # each module needing its own OTel wiring.
    api_logger_provider = LoggerProvider(
        resource=Resource.create({SERVICE_NAME: settings.otel_service_name})
    )
    api_logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=f"{settings.otel_exporter_endpoint}/v1/logs")
        )
    )
    otel_logs.set_logger_provider(api_logger_provider)
    logging.getLogger().addHandler(LoggingHandler(logger_provider=api_logger_provider))
    # Nothing in this app ever called logging.basicConfig(), so root has no
    # console handler of its own — app-level `logging.getLogger(__name__)`
    # calls (task persistence, activity logging, etc.) were only ever
    # visible via OTel, never in `docker compose logs`. Give root a plain
    # StreamHandler too so those show up in both places, same as uvicorn's.
    # Skip records from "uvicorn"/"uvicorn.access" here — they already get
    # console output from their own dedicated handlers (see below); without
    # the filter, once we make them propagate, they'd print twice.
    console_handler = logging.StreamHandler()
    console_handler.addFilter(lambda record: not record.name.startswith("uvicorn"))
    logging.getLogger().addHandler(console_handler)
    # Root defaults to WARNING, which would filter out our own info-level
    # diagnostics before they ever reach either handler above — INFO is the
    # right floor for an app this size; revisit if third-party libs get
    # noisy at that level.
    if logging.getLogger().getEffectiveLevel() > logging.INFO:
        logging.getLogger().setLevel(logging.INFO)

    # uvicorn/fastapi ship their own dictConfig (applied before this module
    # is even imported — Config.__init__ configures logging, *then* imports
    # the app) that gives "uvicorn" and "uvicorn.access" their own console
    # StreamHandlers with propagate=False. That's what puts "Started server
    # process", "Uvicorn running on...", and the per-request access lines on
    # stdout — and, because propagate is off, keeps them from ever reaching
    # the root handler above, so none of it reached the collector. Flipping
    # propagate back on lets those records still hit their own console
    # handler *and* bubble up to root for OTLP export; "uvicorn.error" has
    # no handler of its own and propagates into "uvicorn", so fixing that
    # one logger covers it too.
    logging.getLogger("uvicorn").propagate = True
    logging.getLogger("uvicorn.access").propagate = True

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(app, tracer_provider=api_tracer_provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=api_tracer_provider)
    try:
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
        AsyncPGInstrumentor().instrument(tracer_provider=api_tracer_provider)
    except Exception as e:
        print(f"asyncpg instrumentation skipped: {e}")

    task_counter = meter.create_counter(
        "finagent.tasks", unit="1", description="Agent tasks by terminal status"
    )
    task_duration_ms = meter.create_histogram(
        "finagent.task.duration", unit="ms", description="Task wall-clock duration"
    )
    llm_tokens_counter = meter.create_counter(
        "finagent.llm.tokens", unit="1", description="LLM tokens by provider/direction"
    )
    llm_call_duration_ms = meter.create_histogram(
        "finagent.llm.call.duration", unit="ms", description="Single LLM completion latency"
    )
    tool_duration_ms = meter.create_histogram(
        "finagent.tool.duration", unit="ms", description="Tool execution latency"
    )
    cache_result_counter = meter.create_counter(
        "finagent.cache.result", unit="1", description="query_data cache hits/misses"
    )

    rum_click_counter = rum_meter.create_counter(
        "finagent.rum.clicks", unit="1", description="Clicks by page/element"
    )
    rum_page_timing_ms = rum_meter.create_histogram(
        "finagent.rum.page_load", unit="ms", description="Navigation-timing phases (ttfb/dcl/load)"
    )
    rum_page_view_duration_ms = rum_meter.create_histogram(
        "finagent.rum.page_view.duration", unit="ms", description="Time spent per SPA view"
    )

    global _cls_gauge
    for vital in ("lcp", "inp", "ttfb", "fcp"):
        _web_vital_histograms[vital] = rum_meter.create_histogram(
            vital, unit="ms", description=f"Core Web Vital: {vital.upper()}"
        )
    _cls_gauge = rum_meter.create_gauge("cls", unit="", description="Core Web Vital: Cumulative Layout Shift")


def record_rum_event(event: dict) -> None:
    """Turns one client-relayed RUM event into a span + metric point on the
    finagent-web service. Best-effort: malformed events are dropped, never
    raised into the request handler."""
    etype = event.get("type")
    page = str(event.get("page") or "unknown")[:80]
    client_t_ms = event.get("t")
    end_time_ns = int(client_t_ms * 1_000_000) if isinstance(client_t_ms, (int, float)) else time.time_ns()

    if etype == "click":
        target = event.get("target") or {}
        if rum_click_counter is not None:
            rum_click_counter.add(1, {"page": page, "tag": str(target.get("tag") or "")[:40]})
        span = rum_tracer.start_span(
            "user.click", start_time=end_time_ns,
            attributes={
                "rum.page": page,
                "rum.session_id": str(event.get("session_id") or ""),
                "rum.click.x": event.get("x") or 0,
                "rum.click.y": event.get("y") or 0,
                "rum.click.tag": str(target.get("tag") or ""),
                "rum.click.label": str(target.get("label") or "")[:120],
                "rum.click.id": str(target.get("id") or ""),
            },
        )
        span.end(end_time_ns)

    elif etype == "page_load":
        phases = {
            "ttfb": event.get("ttfb_ms"),
            "dom_content_loaded": event.get("dom_content_loaded_ms"),
            "load": event.get("load_ms"),
        }
        for phase, value in phases.items():
            if isinstance(value, (int, float)) and rum_page_timing_ms is not None:
                rum_page_timing_ms.record(value, {"page": page, "phase": phase})
        span = rum_tracer.start_span(
            "page.load", start_time=end_time_ns,
            attributes={"rum.page": page, "rum.session_id": str(event.get("session_id") or ""), **phases},
        )
        span.end(end_time_ns)

    elif etype == "web_vital":
        name = str(event.get("name") or "").lower()
        value = event.get("value")
        if not isinstance(value, (int, float)):
            return
        attrs = {"page": page, "rating": str(event.get("rating") or "")}
        if name == "cls":
            if _cls_gauge is not None:
                _cls_gauge.set(value, attrs)
        elif name in _web_vital_histograms:
            _web_vital_histograms[name].record(value, attrs)
        span = rum_tracer.start_span(
            "web_vital", start_time=end_time_ns,
            attributes={"rum.page": page, "rum.web_vital.name": name, "rum.web_vital.value": value},
        )
        span.end(end_time_ns)

    elif etype == "page_view":
        duration_ms = event.get("duration_ms")
        if isinstance(duration_ms, (int, float)):
            if rum_page_view_duration_ms is not None:
                rum_page_view_duration_ms.record(duration_ms, {"page": page})
            start_time_ns = end_time_ns - int(duration_ms * 1_000_000)
            span = rum_tracer.start_span(
                "page.view", start_time=start_time_ns,
                attributes={
                    "rum.page": page,
                    "rum.session_id": str(event.get("session_id") or ""),
                    "rum.page_view.duration_ms": duration_ms,
                },
            )
            span.end(end_time_ns)
