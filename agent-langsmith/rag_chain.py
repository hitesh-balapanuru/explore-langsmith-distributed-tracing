import os

from langchain_anthropic import ChatAnthropic
from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

PERSIST_DIR = "/faiss-index"

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


def format_docs(docs) -> str:
    return "\n\n".join(doc.page_content for doc in docs)


def build_retriever():
    """Kept separate from build_answer_chain() so the retrieval step can be
    traced/duplicated to its own project independently of the prompt|model
    step."""
    embeddings = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    vectorstore = FAISS.load_local(
        PERSIST_DIR, embeddings, allow_dangerous_deserialization=True
    )
    return vectorstore.as_retriever(search_kwargs={"k": 4})


def build_answer_chain():
    model = ChatAnthropic(
        model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=1024,
    )
    return PROMPT | model | StrOutputParser()
