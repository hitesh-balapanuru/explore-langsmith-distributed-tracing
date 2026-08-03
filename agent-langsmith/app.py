"""agent-langsmith: LangChain RAG agent traced natively by the LangSmith SDK.

Distributed tracing: the frontend originates a LangSmith RunTree and sends
its `langsmith-trace` / `baggage` headers. We parse those headers with
RunTree.from_headers and pass the resulting run as the `parent` of our own
@traceable-wrapped handler, so this call shows up as a *child* of the
frontend's run in the same LangSmith trace, posted to LANGSMITH_PROJECT (the
"distributed" project).

Multi-project fan-out: on top of that single connected trace, we ALSO log
two deliberate duplicates -- a flat summary run in LANGSMITH_PROJECT_AGENT
(this service's own project) and a flat summary run in
LANGSMITH_PROJECT_VECTORDB (retrieval only). Both are manually constructed
from data already produced by the ONE chain execution above (no second
Anthropic call).

Note these are each their OWN root run (their own trace_id), not the same
trace_id as the connected trace: LangSmith's create_run API rejects an
explicit trace_id on an unparented run unless you also supply a matching
`dotted_order` (confirmed against a real account -- `400 invalid
dotted_order`). So correlation across all four projects here relies on the
shared `request_id` metadata field, not trace_id equality. The real
distributed trace_id is also stamped into metadata for reference.
"""

import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from langsmith import Client, traceable
from langsmith.run_helpers import get_current_run_tree
from langsmith.run_trees import RunTree
from pydantic import BaseModel

from rag_chain import build_answer_chain, build_retriever, format_docs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent-langsmith")

app = FastAPI(title="agent-langsmith")
retriever = build_retriever()
answer_chain = build_answer_chain()
client = Client()

AGENT_PROJECT = os.environ.get("LANGSMITH_PROJECT_AGENT", "agent")
VECTORDB_PROJECT = os.environ.get("LANGSMITH_PROJECT_VECTORDB", "vector_database")


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str


@traceable(name="agent-langsmith.answer_question", run_type="chain")
def answer_question(question: str):
    docs = retriever.invoke(question)
    context = format_docs(docs)
    answer = answer_chain.invoke({"question": question, "context": context})
    current_run = get_current_run_tree()
    trace_id = str(current_run.trace_id) if current_run is not None else None
    return answer, docs, trace_id


def _log_duplicate_run(
    project_name: str,
    name: str,
    inputs: dict,
    outputs: dict,
    distributed_trace_id: str,
    request_id: str,
) -> None:
    """Manually posts a single, flat, already-completed run to another
    project, as its own root (own trace_id) -- LangSmith rejects a
    forced/mismatched trace_id on an unparented run. This intentionally does
    NOT re-run any LangChain step -- it just logs a record of work that
    already happened, so LangSmith API version differences in
    create_run/update_run are the only risk here, not extra cost."""
    run_id = uuid.uuid4()
    try:
        client.create_run(
            id=run_id,
            name=name,
            run_type="chain",
            inputs=inputs,
            project_name=project_name,
            start_time=datetime.now(timezone.utc),
            extra={
                "metadata": {
                    "request_id": request_id,
                    "distributed_trace_id": distributed_trace_id,
                }
            },
            tags=["duplicate", "instrumentation:langsmith-sdk"],
        )
        client.update_run(
            run_id, outputs=outputs, end_time=datetime.now(timezone.utc)
        )
    except Exception:
        logger.exception("Failed to log duplicate run to project %s", project_name)


@app.post("/query", response_model=QueryResponse)
async def query(body: QueryRequest, request: Request) -> QueryResponse:
    headers = dict(request.headers)
    parent_run = RunTree.from_headers(headers)
    request_id = headers.get("x-request-id", str(uuid.uuid4()))

    if parent_run is not None:
        logger.info("Continuing distributed trace %s", parent_run.trace_id)
        answer, docs, trace_id = answer_question(
            body.question, langsmith_extra={"parent": parent_run}
        )
    else:
        logger.info("No incoming trace context; starting a new root trace")
        answer, docs, trace_id = answer_question(body.question)

    if trace_id is None:
        trace_id = str(uuid.uuid4())

    _log_duplicate_run(
        AGENT_PROJECT,
        "agent-langsmith.answer_question",
        {"question": body.question},
        {"answer": answer},
        trace_id,
        request_id,
    )
    _log_duplicate_run(
        VECTORDB_PROJECT,
        "agent-langsmith.retrieve",
        {"question": body.question},
        {
            "count": len(docs),
            "documents": [doc.page_content[:200] for doc in docs],
        },
        trace_id,
        request_id,
    )

    return QueryResponse(answer=answer)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
