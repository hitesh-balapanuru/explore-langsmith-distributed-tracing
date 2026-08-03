"""Builds a local Chroma index from the shared docs/ corpus.

Runs once as a one-shot container before the agent services start, so both
agent-langsmith and agent-openllmetry can mount the resulting index read-only
instead of each re-computing embeddings on startup.
"""

import pathlib

from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_text_splitters import MarkdownTextSplitter

DOCS_DIR = pathlib.Path("/docs")
PERSIST_DIR = "/chroma-data"


def main() -> None:
    loader = DirectoryLoader(
        str(DOCS_DIR), glob="**/*.md", loader_cls=TextLoader, show_progress=True
    )
    documents = loader.load()
    print(f"Loaded {len(documents)} documents from {DOCS_DIR}")

    splitter = MarkdownTextSplitter(chunk_size=800, chunk_overlap=100)
    chunks = splitter.split_documents(documents)
    print(f"Split into {len(chunks)} chunks")

    embeddings = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")

    Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=PERSIST_DIR,
        collection_name="ai-rmf-docs",
    )
    print(f"Wrote Chroma index to {PERSIST_DIR}")


if __name__ == "__main__":
    main()
