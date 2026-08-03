/**
 * Originates a distributed trace using standard OpenTelemetry W3C
 * `traceparent` propagation. Spans recorded here are exported via OTLP
 * straight to LangSmith's OTLP endpoint, and agent-openllmetry extracts the
 * `traceparent` header to continue the same trace with Traceloop-recorded
 * spans as children.
 *
 * Multi-project fan-out: the real span is exported to LANGSMITH_PROJECT
 * ("distributed") only. A second, dedicated single-exporter TracerProvider
 * is used to emit a *separate* duplicate span into LANGSMITH_PROJECT_FRONTEND
 * after the real span ends -- sharing the same trace_id but with a fresh
 * span_id (via `trace.wrapSpanContext` as a fake parent). Registering a
 * second SpanProcessor on the SAME provider and re-exporting the identical
 * span object was tried first and rejected by LangSmith with `409 Run
 * create payload already received` -- its OTLP ingestion treats
 * (trace_id, span_id) as a globally unique run, not scoped per project.
 */

import {
  trace,
  context,
  propagation,
  Span,
  SpanContext,
} from "@opentelemetry/api";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { Resource } from "@opentelemetry/resources";
import { SimpleSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";
import { SemanticResourceAttributes } from "@opentelemetry/semantic-conventions";

const endpoint =
  process.env.LANGSMITH_OTEL_ENDPOINT || "https://api.smith.langchain.com/otel";
const apiKey = process.env.LANGSMITH_API_KEY || "";

function otlpExporter(projectName: string): OTLPTraceExporter {
  return new OTLPTraceExporter({
    url: `${endpoint}/v1/traces`,
    headers: { "x-api-key": apiKey, "Langsmith-Project": projectName },
  });
}

const resource = new Resource({
  [SemanticResourceAttributes.SERVICE_NAME]: "frontend-trace-origin",
});

const provider = new NodeTracerProvider({ resource });
provider.addSpanProcessor(
  new SimpleSpanProcessor(otlpExporter(process.env.LANGSMITH_PROJECT || "distributed"))
);
provider.register();

const tracer = trace.getTracer("frontend-trace-origin");

const frontendProvider = new NodeTracerProvider({ resource });
frontendProvider.addSpanProcessor(
  new SimpleSpanProcessor(
    otlpExporter(process.env.LANGSMITH_PROJECT_FRONTEND || "frontend")
  )
);
const frontendTracer = frontendProvider.getTracer("frontend-trace-origin.duplicates");

function duplicateSpan(
  name: string,
  realSpanContext: SpanContext,
  startTime: number,
  endTime: number,
  attributes: Record<string, string | number>
): void {
  const fakeParentContext = trace.setSpan(
    context.active(),
    trace.wrapSpanContext(realSpanContext)
  );
  const span = frontendTracer.startSpan(name, { startTime }, fakeParentContext);
  span.setAttributes(attributes);
  span.end(endTime);
}

export async function withOtelOrigin<T>(
  question: string,
  requestId: string,
  fn: (headers: Record<string, string>) => Promise<T>
): Promise<{ result: T; traceId: string }> {
  const startTime = Date.now();
  return tracer.startActiveSpan(
    "frontend.answer_question",
    async (span: Span) => {
      span.setAttribute("airmf.question", question);
      span.setAttribute("langsmith.metadata.request_id", requestId);
      const headers: Record<string, string> = { "x-request-id": requestId };
      propagation.inject(context.active(), headers);
      const spanContext = span.spanContext();
      const traceId = spanContext.traceId;

      try {
        const result = await fn(headers);
        return { result, traceId };
      } catch (err) {
        span.recordException(err as Error);
        throw err;
      } finally {
        span.end();
        duplicateSpan(
          "frontend.answer_question",
          spanContext,
          startTime,
          Date.now(),
          {
            "airmf.question": question,
            "langsmith.metadata.request_id": requestId,
          }
        );
      }
    }
  );
}
