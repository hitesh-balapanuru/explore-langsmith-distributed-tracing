/**
 * Originates a distributed trace using the LangSmith SDK's own propagation
 * format (the `langsmith-trace` / `baggage` headers). The RunTree created
 * here becomes the *parent* run; agent-langsmith continues it server-side
 * via `RunTree.from_headers`, so the whole request shows up as one trace in
 * LangSmith spanning this Node process and the Python agent.
 */

import { Client, RunTree } from "langsmith";

const client = new Client({
  apiKey: process.env.LANGSMITH_API_KEY,
  apiUrl: process.env.LANGSMITH_ENDPOINT || "https://api.smith.langchain.com",
});

export async function withLangsmithOrigin<T>(
  question: string,
  fn: (headers: Record<string, string>) => Promise<T>
): Promise<{ result: T; traceId: string }> {
  const parentRun = new RunTree({
    name: "frontend.answer_question",
    run_type: "chain",
    inputs: { question },
    project_name: process.env.LANGSMITH_PROJECT || "explore-langsmith-tracing",
    client,
  });

  await parentRun.postRun();

  const headers = parentRun.toHeaders() as unknown as Record<string, string>;

  try {
    const result = await fn(headers);
    await parentRun.end({ outputs: { result } });
    return { result, traceId: parentRun.trace_id as string };
  } catch (err) {
    await parentRun.end({ error: String(err) });
    throw err;
  } finally {
    await parentRun.patchRun();
  }
}
