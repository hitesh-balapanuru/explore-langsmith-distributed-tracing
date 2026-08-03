"""Initializes OpenLLMetry (Traceloop SDK) with traces exported directly to
LangSmith's OTLP ingestion endpoint.

This must run before `rag_chain.build_chain()` is called, since Traceloop
patches the LangChain/Anthropic clients on init to auto-instrument them.
"""

import os

from traceloop.sdk import Traceloop


def init_tracing() -> None:
    endpoint = os.environ.get(
        "LANGSMITH_OTEL_ENDPOINT", "https://api.smith.langchain.com/otel"
    )
    api_key = os.environ["LANGSMITH_API_KEY"]
    project = os.environ.get("LANGSMITH_PROJECT", "explore-langsmith-tracing")

    Traceloop.init(
        app_name="agent-openllmetry",
        api_endpoint=endpoint,
        headers={
            "x-api-key": api_key,
            "Langsmith-Project": project,
        },
        disable_batch=True,
    )
