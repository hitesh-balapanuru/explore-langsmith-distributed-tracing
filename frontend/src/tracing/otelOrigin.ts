/**
 * Originates a distributed trace using standard OpenTelemetry W3C
 * `traceparent` propagation. Spans recorded here are exported via OTLP
 * straight to LangSmith's OTLP endpoint, and agent-openllmetry extracts the
 * `traceparent` header to continue the same trace with Traceloop-recorded
 * spans as children.
 *
 * Multi-project fan-out: the TracerProvider below has TWO span processors --
 * one exporting to LANGSMITH_PROJECT ("distributed", the connected trace),
 * one exporting to LANGSMITH_PROJECT_FRONTEND (a standalone per-service
 * copy). Both come from the same span, so they share a trace_id for free;
 * we also stamp a `request_id` attribute as a metadata-based fallback for
 * correlating across projects, since LangSmith has no native cross-project
 * trace view.
 */

import { trace, context, propagation, Span } from "@opentelemetry/api";
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

const provider = new NodeTracerProvider({
  resource: new Resource({
    [SemanticResourceAttributes.SERVICE_NAME]: "frontend-trace-origin",
  }),
});

provider.addSpanProcessor(
  new SimpleSpanProcessor(otlpExporter(process.env.LANGSMITH_PROJECT || "distributed"))
);
provider.addSpanProcessor(
  new SimpleSpanProcessor(
    otlpExporter(process.env.LANGSMITH_PROJECT_FRONTEND || "frontend")
  )
);
provider.register();

const tracer = trace.getTracer("frontend-trace-origin");

export async function withOtelOrigin<T>(
  question: string,
  requestId: string,
  fn: (headers: Record<string, string>) => Promise<T>
): Promise<{ result: T; traceId: string }> {
  return tracer.startActiveSpan(
    "frontend.answer_question",
    async (span: Span) => {
      span.setAttribute("airmf.question", question);
      span.setAttribute("langsmith.metadata.request_id", requestId);
      const headers: Record<string, string> = { "x-request-id": requestId };
      propagation.inject(context.active(), headers);
      const traceId = span.spanContext().traceId;

      try {
        const result = await fn(headers);
        return { result, traceId };
      } catch (err) {
        span.recordException(err as Error);
        throw err;
      } finally {
        span.end();
      }
    }
  );
}
