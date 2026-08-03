/**
 * Originates a distributed trace using the LangSmith SDK's own propagation
 * format (the `langsmith-trace` / `baggage` headers). The RunTree created
 * here becomes the *parent* run; agent-langsmith continues it server-side
 * via `RunTree.from_headers`, so the whole request shows up as one trace in
 * LANGSMITH_PROJECT ("distributed") spanning this Node process and the
 * Python agent.
 *
 * Multi-project fan-out: after that connected trace completes, we ALSO post
 * a second, standalone RunTree to LANGSMITH_PROJECT_FRONTEND, a per-service
 * copy. It gets its OWN trace_id (its own root) rather than reusing the
 * connected trace's -- LangSmith's create_run API rejects an
 * explicit/mismatched trace_id on an unparented run unless a matching
 * `dotted_order` is also supplied (confirmed against a real account: `400
 * invalid dotted_order`). So correlation with the connected trace relies on
 * the shared `request_id` metadata field (plus the real trace_id, stamped
 * into metadata for reference), not trace_id equality.
 */

import { Client, RunTree } from "langsmith";

const client = new Client({
  apiKey: process.env.LANGSMITH_API_KEY,
  apiUrl: process.env.LANGSMITH_ENDPOINT || "https://api.smith.langchain.com",
});

const DISTRIBUTED_PROJECT = process.env.LANGSMITH_PROJECT || "distributed";
const FRONTEND_PROJECT = process.env.LANGSMITH_PROJECT_FRONTEND || "frontend";

export async function withLangsmithOrigin<T>(
  question: string,
  requestId: string,
  fn: (headers: Record<string, string>) => Promise<T>
): Promise<{ result: T; traceId: string }> {
  const parentRun = new RunTree({
    name: "frontend.answer_question",
    run_type: "chain",
    inputs: { question },
    project_name: DISTRIBUTED_PROJECT,
    client,
    extra: { metadata: { request_id: requestId } },
  });

  await parentRun.postRun();

  const headers = parentRun.toHeaders() as unknown as Record<string, string>;
  headers["x-request-id"] = requestId;

  let result: T;
  try {
    result = await fn(headers);
    await parentRun.end({ outputs: { result } });
  } catch (err) {
    await parentRun.end({ error: String(err) });
    throw err;
  } finally {
    await parentRun.patchRun();
  }

  const traceId = parentRun.trace_id as string;

  const frontendCopy = new RunTree({
    name: "frontend.answer_question",
    run_type: "chain",
    inputs: { question },
    project_name: FRONTEND_PROJECT,
    client,
    extra: {
      metadata: { request_id: requestId, distributed_trace_id: traceId },
    },
    tags: ["duplicate", "instrumentation:langsmith-sdk"],
  });
  await frontendCopy.postRun();
  await frontendCopy.end({ outputs: { result } });
  await frontendCopy.patchRun();

  return { result, traceId };
}
