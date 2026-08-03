import { randomUUID } from "crypto";
import express, { Request, Response } from "express";
import path from "path";

import { withLangsmithOrigin } from "./tracing/langsmithOrigin";
import { withOtelOrigin } from "./tracing/otelOrigin";

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, "..", "src", "public")));

const AGENT_LANGSMITH_URL =
  process.env.AGENT_LANGSMITH_URL || "http://agent-langsmith:8001";
const AGENT_OPENLLMETRY_URL =
  process.env.AGENT_OPENLLMETRY_URL || "http://agent-openllmetry:8002";

async function callAgent(
  url: string,
  question: string,
  headers: Record<string, string>
): Promise<string> {
  const resp = await fetch(`${url}/query`, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify({ question }),
  });
  if (!resp.ok) {
    throw new Error(`Agent responded ${resp.status}: ${await resp.text()}`);
  }
  const data = (await resp.json()) as { answer: string };
  return data.answer;
}

app.post("/api/chat/langsmith", async (req: Request, res: Response) => {
  const { question } = req.body as { question?: string };
  if (!question) {
    res.status(400).json({ error: "question is required" });
    return;
  }

  const requestId = randomUUID();
  try {
    const { result: answer, traceId } = await withLangsmithOrigin(
      question,
      requestId,
      (headers) => callAgent(AGENT_LANGSMITH_URL, question, headers)
    );
    res.json({ answer, traceId, requestId, instrumentation: "langsmith-sdk" });
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
});

app.post("/api/chat/openllmetry", async (req: Request, res: Response) => {
  const { question } = req.body as { question?: string };
  if (!question) {
    res.status(400).json({ error: "question is required" });
    return;
  }

  const requestId = randomUUID();
  try {
    const { result: answer, traceId } = await withOtelOrigin(
      question,
      requestId,
      (headers) => callAgent(AGENT_OPENLLMETRY_URL, question, headers)
    );
    res.json({ answer, traceId, requestId, instrumentation: "openllmetry-otel" });
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
});

app.get("/health", (_req, res) => res.json({ status: "ok" }));

const PORT = Number(process.env.PORT || 3000);
app.listen(PORT, () => {
  console.log(`trace-origin-frontend listening on :${PORT}`);
});
