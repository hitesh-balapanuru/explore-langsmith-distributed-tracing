/**
 * Originates a distributed trace using the LangSmith SDK's own propagation
 * format (the `langsmith-trace` / `baggage` headers). The RunTree created
 * here becomes the *parent* run; agent-langsmith continues it server-side
 * via `RunTree.from_headers`, so the whole request shows up as one trace in
 * the "distributed" project spanning this Node process and the Python
 * agent.
 *
 * Multi-project fan-out: uses RunTree's native `replicas` field instead of
 * manually posting a second RunTree. A replica list REPLACES the default
 * single destination entirely, so "distributed" has to be listed explicitly
 * (`primary: true`, keeping the real run/trace ids) alongside "frontend"
 * (`reroot: true`, giving it its own deterministically-derived id instead of
 * colliding with the primary's). `postRun`/`patchRun` iterate the replica
 * list and post to each automatically -- no second RunTree, no manual
 * request_id/distributed_trace_id correlation fields needed.
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
    replicas: [
      { projectName: DISTRIBUTED_PROJECT, primary: true },
      { projectName: FRONTEND_PROJECT, reroot: true },
    ],
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

  return { result, traceId: parentRun.trace_id as string };
}
