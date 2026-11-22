import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from langchain_core.runnables import RunnableLambda

from app.services.rag_service import RAGService


class FakeEmbeddings:
    def __init__(self):
        self.queries = []

    def embed_query(self, text):
        self.queries.append(text)
        return [0.1, 0.2, 0.3]


class FakeCollection:
    def __init__(self, results):
        self.results = results
        self.query_args = None
        self.get_args = None

    def query(self, **kwargs):
        self.query_args = kwargs
        return self.results

    def get(self, **kwargs):
        self.get_args = kwargs
        return {
            "ids": self.results.get("ids", [[]])[0],
            "documents": self.results.get("documents", [[]])[0],
            "metadatas": self.results.get("metadatas", [[]])[0],
        }


def make_service(results, answer="The contract renews annually."):
    service = object.__new__(RAGService)
    service.embeddings = FakeEmbeddings()
    service.collection = FakeCollection(results)
    service.llm = RunnableLambda(
        lambda prompt: SimpleNamespace(
            content=answer if prompt.to_string() else answer,
            text=answer,
        )
    )
    return service


class RagServiceTests(unittest.TestCase):
    def test_retrieves_chunks_and_returns_cited_source(self):
        results = {
            "ids": [["chunk-1"]],
            "documents": [["The contract renews each year."]],
            "metadatas": [
                [
                    {
                        "chunk_id": "chunk-1",
                        "document_id": "document-1",
                        "document_name": "contract.pdf",
                        "page_number": 3,
                    }
                ]
            ],
            "distances": [[0.12]],
        }
        service = make_service(results)

        response = service.query("When does it renew?", doc_ids=["document-1"], k=3)

        self.assertEqual(response["answer"], "The contract renews annually.")
        self.assertTrue(response["has_sufficient_context"])
        self.assertEqual(response["sources"][0]["document_name"], "contract.pdf")
        self.assertEqual(response["sources"][0]["page_number"], 3)
        self.assertAlmostEqual(response["sources"][0]["score"], 0.88)
        self.assertEqual(service.embeddings.queries, ["When does it renew?"])
        self.assertEqual(
            service.collection.query_args["where"],
            {"document_id": {"$in": ["document-1"]}},
        )
        self.assertEqual(service.collection.query_args["n_results"], 10)
        self.assertEqual(
            service.collection.get_args["where"],
            {"document_id": {"$in": ["document-1"]}},
        )

    def test_returns_not_found_response_without_calling_llm(self):
        service = make_service(
            {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        )
        service.llm = Mock()

        response = service.query("What is the renewal date?")

        self.assertFalse(response["has_sufficient_context"])
        self.assertEqual(response["sources"], [])
        self.assertIn("not contain enough information", response["answer"])
        service.llm.invoke.assert_not_called()

    def test_handles_text_blocks_in_gemini_response(self):
        results = {
            "ids": [["chunk-1"]],
            "documents": [["The contract renews each year."]],
            "metadatas": [
                [
                    {
                        "chunk_id": "chunk-1",
                        "document_id": "document-1",
                        "document_name": "contract.pdf",
                        "page_number": 1,
                    }
                ]
            ],
            "distances": [[0.1]],
        }
        service = make_service(results)
        service.llm = RunnableLambda(
            lambda prompt: SimpleNamespace(
                content=[{"type": "text", "text": "The contract renews annually."}],
                text=(
                    "The contract renews annually."
                    if prompt.to_string()
                    else ""
                ),
            )
        )

        response = service.query(
            "How often does it renew?",
            doc_ids=["document-1"],
        )

        self.assertEqual(response["answer"], "The contract renews annually.")
        self.assertTrue(response["has_sufficient_context"])

    def test_refuses_unscoped_query_before_searching_chroma(self):
        service = make_service(
            {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        )

        response = service.query("What is in another workspace?")

        self.assertEqual(response["sources"], [])
        self.assertEqual(service.embeddings.queries, [])
        self.assertIsNone(service.collection.query_args)

    def test_uses_bm25_only_chunks_and_falls_back_when_reranking_fails(self):
        results = {
            "ids": [["dense-1"]],
            "documents": [["General information about purchasing."]],
            "metadatas": [[
                {
                    "chunk_id": "dense-1",
                    "document_id": "document-1",
                    "document_name": "policy.pdf",
                    "page_number": 1,
                }
            ]],
            "distances": [[0.2]],
        }
        service = make_service(results)
        service.collection.get = Mock(return_value={
            "ids": ["dense-1", "exact-1"],
            "documents": [
                "General information about purchasing.",
                "FIN-AP-204 requires two manager approvals for purchases over $5,000.",
            ],
            "metadatas": [
                results["metadatas"][0][0],
                {
                    "chunk_id": "exact-1",
                    "document_id": "document-1",
                    "document_name": "policy.pdf",
                    "page_number": 4,
                },
            ],
        })
        service.llm = RunnableLambda(
            lambda _prompt: SimpleNamespace(
                content="Two approvals are required.",
                text="Two approvals are required.",
            )
        )

        with patch(
            "app.services.rag_service.rerank",
            side_effect=RuntimeError("model unavailable"),
        ):
            response = service.query(
                "What does FIN-AP-204 require?",
                doc_ids=["document-1"],
            )

        self.assertTrue(
            any(source["page_number"] == 4 for source in response["sources"])
        )
        self.assertTrue(
            any("FIN-AP-204" in source["chunk_text"] for source in response["sources"])
        )
        self.assertLessEqual(len(response["sources"]), 4)


if __name__ == "__main__":
    unittest.main()
