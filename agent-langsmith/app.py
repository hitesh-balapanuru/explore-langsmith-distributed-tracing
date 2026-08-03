"""agent-langsmith: LangChain RAG agent traced natively by the LangSmith SDK.

Distributed tracing: the frontend originates a LangSmith RunTree and sends
its `langsmith-trace` / `baggage` headers on the request. We parse those
headers with RunTree.from_headers and pass the resulting run as the `parent`
of our own @traceable-wrapped handler, so this call shows up as a *child* of
the frontend's run in the same LangSmith trace instead of a new root trace.
"""

import logging

from fastapi import FastAPI, Request
from langsmith import traceable
from langsmith.run_trees import RunTree
from pydantic import BaseModel

from rag_chain import build_chain

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent-langsmith")

app = FastAPI(title="agent-langsmith")
chain = build_chain()


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str


@traceable(name="agent-langsmith.answer_question", run_type="chain")
def answer_question(question: str) -> str:
    return chain.invoke(question)


@app.post("/query", response_model=QueryResponse)
async def query(body: QueryRequest, request: Request) -> QueryResponse:
    parent_run = RunTree.from_headers(dict(request.headers))

    if parent_run is not None:
        logger.info("Continuing distributed trace %s", parent_run.trace_id)
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
