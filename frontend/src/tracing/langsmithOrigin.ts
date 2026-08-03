/**
 * Originates a distributed trace using the LangSmith SDK's own propagation
 * format (the `langsmith-trace` / `baggage` headers). The RunTree created
 * here becomes the *parent* run; agent-langsmith continues it server-side
 * via `RunTree.from_headers`, so the whole request shows up as one trace in
 * LANGSMITH_PROJECT ("distributed") spanning this Node process and the
 * Python agent.
 *
 * Multi-project fan-out: after that connected trace completes, we ALSO post
 * a second, standalone RunTree (no parent) to LANGSMITH_PROJECT_FRONTEND, a
 * per-service copy sharing the same trace_id (plus a `request_id` metadata
 * field) purely so it can be correlated with the connected trace by hand --
 * LangSmith has no native cross-project trace view.
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
    trace_id: traceId,
    client,
    extra: { metadata: { request_id: requestId } },
    tags: ["duplicate", "instrumentation:langsmith-sdk"],
  });
  await frontendCopy.postRun();
  await frontendCopy.end({ outputs: { result } });
  await frontendCopy.patchRun();

  return { result, traceId };
}
