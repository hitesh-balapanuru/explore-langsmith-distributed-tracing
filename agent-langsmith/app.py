"""agent-langsmith: LangChain RAG agent traced natively by the LangSmith SDK.

Distributed tracing: the frontend originates a LangSmith RunTree and sends
its `langsmith-trace` / `baggage` headers. We parse those headers with
RunTree.from_headers and pass the resulting run as the `parent` of our own
@traceable-wrapped handler, so this call shows up as a *child* of the
frontend's run in the same LangSmith trace, posted to the "distributed"
project.

Multi-project fan-out: uses LangSmith's native `tracing_context(replicas=
[...])` mechanism instead of manually duplicating runs via
`Client.create_run`/`update_run`. A replica list REPLACES the default single
destination entirely, so "distributed" has to be listed explicitly (as
`primary=True`, keeping the real run/trace ids) alongside each extra
destination. Extra destinations use `reroot=True`, which strips the parent
link and makes that replica its own independent trace root in that project
-- otherwise a replica whose parent run only exists in "distributed" would
hit the same `400 invalid dotted_order` that hand-rolled duplicate-posting
ran into. `reroot` computes a deterministic secondary run_id from the
primary's, so this replaces the `request_id`/`distributed_trace_id`
metadata correlation hack entirely.

Gotcha confirmed by reading the SDK source (langsmith/run_helpers.py
_setup_run + run_trees.py create_child): when a @traceable call has a
parent (via `langsmith_extra={"parent": ...}` or the ambient current run),
its RunTree is built via `parent.create_child(...)`, which sets
`replicas=self.replicas` -- i.e. it inherits from the PARENT OBJECT's own
`.replicas` attribute, NOT from the ambient `tracing_context(replicas=...)`
contextvar. Only a parentless run reads `_REPLICAS.get()` (the contextvar)
directly. LangChain's own callback-based auto-tracing (the
ChatAnthropic/ChatPromptTemplate/etc spans) goes through a different path
that DOES read the contextvar fresh, so those already worked. To get our
own answer_question/retrieve wrapper spans to replicate correctly, we set
`.replicas` directly on the relevant parent RunTree object before calling
the child -- confirmed working against a real account.
"""

import logging
import os

from fastapi import FastAPI, Request
from langsmith import traceable, tracing_context
from langsmith.run_helpers import get_current_run_tree
from langsmith.run_trees import RunTree
from pydantic import BaseModel

from rag_chain import build_answer_chain, build_retriever, format_docs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent-langsmith")

app = FastAPI(title="agent-langsmith")
retriever = build_retriever()
answer_chain = build_answer_chain()

DISTRIBUTED_PROJECT = os.environ.get("LANGSMITH_PROJECT", "distributed")
AGENT_PROJECT = os.environ.get("LANGSMITH_PROJECT_AGENT", "agent")
VECTORDB_PROJECT = os.environ.get("LANGSMITH_PROJECT_VECTORDB", "vector_database")

PRIMARY_REPLICA = {"project_name": DISTRIBUTED_PROJECT, "primary": True}
AGENT_REPLICAS = [PRIMARY_REPLICA, {"project_name": AGENT_PROJECT, "reroot": True}]
VECTORDB_REPLICAS = [PRIMARY_REPLICA, {"project_name": VECTORDB_PROJECT, "reroot": True}]


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str


@traceable(name="agent-langsmith.retrieve", run_type="chain")
def retrieve(question: str) -> dict:
    docs = retriever.invoke(question)
    return {
        "context": format_docs(docs),
        "count": len(docs),
        "documents": [doc.page_content[:200] for doc in docs],
    }


@traceable(name="agent-langsmith.answer_question", run_type="chain")
def answer_question(question: str) -> str:
    # retrieve()'s parent is this function's own run, found via the ambient
    # current-run-tree -- create_child() inherits THAT object's .replicas,
    # so we set it directly rather than relying on tracing_context() alone.
    current_run = get_current_run_tree()
    original_replicas = current_run.replicas if current_run is not None else None
    if current_run is not None:
        current_run.replicas = VECTORDB_REPLICAS
    try:
        with tracing_context(replicas=VECTORDB_REPLICAS):
            retrieved = retrieve(question)
    finally:
        if current_run is not None:
            current_run.replicas = original_replicas

    return answer_chain.invoke({"question": question, "context": retrieved["context"]})


@app.post("/query", response_model=QueryResponse)
async def query(body: QueryRequest, request: Request) -> QueryResponse:
    parent_run = RunTree.from_headers(dict(request.headers))

    with tracing_context(replicas=AGENT_REPLICAS):
        if parent_run is not None:
            logger.info("Continuing distributed trace %s", parent_run.trace_id)
            # create_child() (used because a parent is passed below) inherits
            # replicas from parent_run.replicas, not the ambient context --
            # headers don't carry replica config, so set it explicitly.
            parent_run.replicas = AGENT_REPLICAS
            answer = answer_question(
                body.question, langsmith_extra={"parent": parent_run}
            )
        else:
            logger.info("No incoming trace context; starting a new root trace")
            answer = answer_question(body.question)

    return QueryResponse(answer=answer)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
