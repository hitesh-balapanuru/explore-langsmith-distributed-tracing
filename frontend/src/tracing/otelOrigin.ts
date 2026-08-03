/**
 * Originates a distributed trace using standard OpenTelemetry W3C
 * `traceparent` propagation. Spans recorded here are exported via OTLP
 * straight to LangSmith's OTLP endpoint, and agent-openllmetry extracts the
 * `traceparent` header to continue the same trace with Traceloop-recorded
 * spans as children.
 */

import { trace, context, propagation, Span } from "@opentelemetry/api";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { Resource } from "@opentelemetry/resources";
import { SimpleSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";
import { SemanticResourceAttributes } from "@opentelemetry/semantic-conventions";

const endpoint =
  process.env.LANGSMITH_OTEL_ENDPOINT || "https://api.smith.langchain.com/otel";

const exporter = new OTLPTraceExporter({
  url: `${endpoint}/v1/traces`,
  headers: {
    "x-api-key": process.env.LANGSMITH_API_KEY || "",
    "Langsmith-Project": process.env.LANGSMITH_PROJECT || "explore-langsmith-tracing",
  },
});

const provider = new NodeTracerProvider({
  resource: new Resource({
    [SemanticResourceAttributes.SERVICE_NAME]: "frontend-trace-origin",
  }),
});
provider.addSpanProcessor(new SimpleSpanProcessor(exporter));
provider.register();

const tracer = trace.getTracer("frontend-trace-origin");

export async function withOtelOrigin<T>(
  question: string,
  fn: (headers: Record<string, string>) => Promise<T>
): Promise<{ result: T; traceId: string }> {
  return tracer.startActiveSpan(
    "frontend.answer_question",
    async (span: Span) => {
      span.setAttribute("nimbus.question", question);
      const headers: Record<string, string> = {};
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
