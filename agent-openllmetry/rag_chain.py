import os

from langchain_anthropic import ChatAnthropic
from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableParallel, RunnablePassthrough

PERSIST_DIR = "/chroma-data"

PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an assistant answering questions about the NIST AI Risk "
            "Management Framework (AI RMF). Answer the question using only "
            "the provided context. If the context doesn't contain the "
            "answer, say you don't know.\n\nContext:\n{context}",
        ),
        ("human", "{question}"),
    ]
)


def _format_docs(docs) -> str:
    return "\n\n".join(doc.page_content for doc in docs)


def build_chain():
    embeddings = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    vectorstore = Chroma(
        persist_directory=PERSIST_DIR,
        embedding_function=embeddings,
        collection_name="ai-rmf-docs",
    )
    retriever = vectorstore.as_retriever(search_kwargs={"k": 4})

    model = ChatAnthropic(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=1024,
    )

    chain = (
        RunnableParallel(
            context=retriever | _format_docs, question=RunnablePassthrough()
        )
        | PROMPT
        | model
        | StrOutputParser()
    )
    return chain
