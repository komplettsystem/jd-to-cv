"""Traces. Pydantic AI emits OpenTelemetry spans that follow the GenAI semantic conventions. This module
stores them in the app's own SQLite tables (so the admin dashboards can show them) and can also forward them
to any OTLP backend (Langfuse, Arize Phoenix, Logfire, Grafana) when OTEL_EXPORTER_OTLP_ENDPOINT is set.

Traces hold full prompts, so they hold the CV and every posting a visitor pasted. They are admin-only."""
import json
from contextlib import contextmanager
from datetime import datetime, timezone

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import StatusCode
from pydantic_ai.capabilities import Instrumentation
from pydantic_ai.models.instrumented import InstrumentationSettings

from llm import pricing

MAX_ATTR = 200_000  # characters kept per attribute
BILLABLE = ("openrouter", "bedrock", "anthropic")  # providers that charge money per call


def _plain(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value if not isinstance(value, str) else value[:MAX_ATTR]
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)[:MAX_ATTR]


def _iso(ns):
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat(timespec="milliseconds")


class SqliteSpanExporter(SpanExporter):
    def __init__(self, db):
        self.db = db

    def export(self, spans):
        rows, roots = [], []
        for s in spans:
            ctx = s.get_span_context()
            attrs = {k: _plain(v) for k, v in (s.attributes or {}).items()}
            for ev in s.events:
                if ev.name == "exception":
                    attrs["_exception"] = _plain(ev.attributes.get("exception.message", ""))
            status = "error" if s.status.status_code == StatusCode.ERROR else "ok"
            trace_id = format(ctx.trace_id, "032x")
            rows.append((format(ctx.span_id, "016x"), trace_id, format(s.parent.span_id, "016x") if s.parent else None,
                         s.name, str(attrs.get("gen_ai.operation.name", "")), s.start_time, s.end_time, status,
                         json.dumps(attrs)))
            if s.parent is None:
                roots.append((trace_id, s, attrs, status))
        self.db.insert_spans(rows)
        for trace_id, root, attrs, status in roots:
            self._summarise(trace_id, root, attrs, status)
        return SpanExportResult.SUCCESS

    def _summarise(self, trace_id, root, attrs, status):
        spans = self.db.spans_for(trace_id)
        chats = [s for s in spans if s["operation"] == "chat"]
        in_tok = sum(int(s["attrs"].get("gen_ai.usage.input_tokens", 0) or 0) for s in chats)
        out_tok = sum(int(s["attrs"].get("gen_ai.usage.output_tokens", 0) or 0) for s in chats)
        cost = None
        for s in chats:
            a = s["attrs"]
            c = a.get("match.cost_usd")
            if c is None:
                c = pricing.cost_usd(attrs.get("match.provider", ""), attrs.get("match.model_name", ""),
                                 int(a.get("gen_ai.usage.input_tokens", 0) or 0),
                                 int(a.get("gen_ai.usage.output_tokens", 0) or 0),
                                 attrs.get("match.price_in"), attrs.get("match.price_out"))
            if c is not None:
                cost = (cost or 0.0) + c
        errors = [s["attrs"].get("_exception", "") for s in spans if s["status"] == "error"]
        self.db.upsert_trace({
            "trace_id": trace_id, "name": root.name, "started": _iso(root.start_time),
            "duration_ms": (root.end_time - root.start_time) / 1e6, "status": status,
            "error": next((e for e in errors if e), ""), "subject_type": attrs.get("match.subject_type", ""),
            "subject_id": attrs.get("match.subject_id"), "model": attrs.get("match.model_id", ""),
            "input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost, "model_requests": len(chats),
            "tool_calls": sum(1 for s in spans if s["operation"] == "execute_tool"),
            "public": int(bool(attrs.get("match.public"))),
            "billable": int(attrs.get("match.provider", "") in BILLABLE and cost is not None),
        })

    def shutdown(self):
        pass


class Tracing:
    """One tracer provider for the app. `instrumentation` is the capability every agent is built with."""

    def __init__(self, db, settings):
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(SqliteSpanExporter(db)))
        if settings.otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            self.provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        self.tracer = self.provider.get_tracer("match-app")
        self.instrumentation = Instrumentation(settings=InstrumentationSettings(
            tracer_provider=self.provider, include_content=settings.trace_content))

    @contextmanager
    def subject(self, name, subject_type, subject_id, entry, public=False):
        """Root span for one unit of work (a result, a chat answer, an eval case). Yields the trace id."""
        attrs = {"match.subject_type": subject_type, "match.public": bool(public)}
        if subject_id is not None:
            attrs["match.subject_id"] = subject_id
        if entry:
            attrs.update({"match.model_id": entry["id"], "match.provider": entry["provider"],
                          "match.model_name": entry["model_name"]})
            if entry.get("price_in") is not None:
                attrs["match.price_in"] = entry["price_in"]
                attrs["match.price_out"] = entry["price_out"]
        with self.tracer.start_as_current_span(name, attributes=attrs) as span:
            yield format(span.get_span_context().trace_id, "032x")
