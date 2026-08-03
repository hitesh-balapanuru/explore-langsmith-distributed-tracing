/**
 * Originates a distributed trace using standard OpenTelemetry W3C
 * `traceparent` propagation. Spans recorded here are exported via OTLP
 * straight to LangSmith's OTLP endpoint, and agent-openllmetry extracts the
 * `traceparent` header to continue the same trace with Traceloop-recorded
 * spans as children.
 *
 * Multi-project fan-out: the real span is exported to LANGSMITH_PROJECT
 * ("distributed") only. A second, dedicated single-exporter TracerProvider
 * emits a *separate* duplicate span into LANGSMITH_PROJECT_FRONTEND after the
 * real span ends. Two things were tried and confirmed broken against a real
 * LangSmith account before this:
 *
 *   1. A second SpanProcessor on the SAME provider re-exporting the
 *      identical span -> `409 Run create payload already received`
 *      (LangSmith's OTLP ingestion treats trace_id+span_id as a globally
 *      unique run, not scoped per project).
 *   2. A duplicate span sharing the real trace_id (via `trace.wrapSpanContext`
 *      as a fake parent) but a fresh span_id -> accepted, but silently
 *      landed in whichever project's span for that trace_id LangSmith saw
 *      first, ignoring this span's own Langsmith-Project header. Project
 *      ownership appears pinned per trace_id, not per span.
 *
 * What works: the duplicate gets its own independent trace_id (a genuine
 * new root, via `ROOT_CONTEXT`). Correlation with the connected trace relies
 * on `request_id`/`distributed_trace_id` attributes instead of a shared
 * trace_id -- the same fallback the native-SDK side already needed.
 *
 * Input/output: LangSmith's OTLP ingestion reads the `traceloop.entity.input`
 * / `traceloop.entity.output` span attributes (JSON-encoded strings) to
 * populate its Input/Output UI for non-LLM spans -- this is the same
 * convention Traceloop's own Python LangChain instrumentation uses (visible
 * on the nested ChatPromptTemplate/RunnableSequence/StrOutputParser spans
 * agent-openllmetry produces). Without setting these explicitly, our own
 * manually-created spans show up with empty Input/Output despite the nested
 * LLM/chain spans underneath having it populated correctly.
 */

import {
  trace,
  context,
  propagation,
  ROOT_CONTEXT,
  Span,
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

const ENTITY_INPUT = "traceloop.entity.input";
const ENTITY_OUTPUT = "traceloop.entity.output";

function duplicateSpan(
  name: string,
  startTime: number,
  endTime: number,
  attributes: Record<string, string | number>
): void {
  const span = frontendTracer.startSpan(name, { startTime }, ROOT_CONTEXT);
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
      span.setAttribute("langsmith.metadata.request_id", requestId);
      span.setAttribute(ENTITY_INPUT, JSON.stringify({ question }));
      const headers: Record<string, string> = { "x-request-id": requestId };
      propagation.inject(context.active(), headers);
      const traceId = span.spanContext().traceId;
      let result: T | undefined;

      try {
        result = await fn(headers);
        span.setAttribute(ENTITY_OUTPUT, JSON.stringify({ answer: result }));
        return { result, traceId };
      } catch (err) {
        span.recordException(err as Error);
        throw err;
      } finally {
        span.end();
        const duplicateAttributes: Record<string, string> = {
          [ENTITY_INPUT]: JSON.stringify({ question }),
          "langsmith.metadata.request_id": requestId,
          "langsmith.metadata.distributed_trace_id": traceId,
        };
        if (result !== undefined) {
          duplicateAttributes[ENTITY_OUTPUT] = JSON.stringify({ answer: result });
        }
        duplicateSpan(
          "frontend.answer_question",
          startTime,
          Date.now(),
          duplicateAttributes
        );
      }
    }
  );
}
