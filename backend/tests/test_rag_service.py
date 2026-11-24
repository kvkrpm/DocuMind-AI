import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from langchain_core.runnables import RunnableLambda

from app.services.rag_service import RAGService


class FakeEmbeddings:
    def __init__(self):
        self.queries = []
        self.document_batches = []

    def embed_query(self, text):
        self.queries.append(text)
        return [0.1, 0.2, 0.3]

    def embed_documents(self, texts):
        self.document_batches.append(texts)
        return [[float(index)] for index, _ in enumerate(texts)]


class FakeCollection:
    def __init__(self, results):
        self.results = results
        self.query_args = None
        self.get_args = None
        self.add_args = None

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

    def add(self, **kwargs):
        self.add_args = kwargs


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
    def test_add_chunks_batches_embedding_requests(self):
        service = make_service(
            {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        )
        chunks = [
            {"text": f"chunk {index}", "metadata": {"chunk_id": str(index)}}
            for index in range(51)
        ]

        count = service.add_chunks(chunks)

        self.assertEqual(count, 51)
        self.assertEqual(
            [len(batch) for batch in service.embeddings.document_batches],
            [25, 25, 1],
        )
        self.assertEqual(len(service.collection.add_args["embeddings"]), 51)

    def test_add_chunks_retries_rate_limited_batch_with_exponential_backoff(self):
        service = make_service(
            {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        )
        rate_limit_error = RuntimeError("RESOURCE_EXHAUSTED: 429")
        service.embeddings.embed_documents = Mock(
            side_effect=[rate_limit_error, rate_limit_error, [[0.1]]]
        )

        with patch("app.services.rag_service.time.sleep") as sleep:
            count = service.add_chunks([
                {"text": "chunk", "metadata": {"chunk_id": "chunk-1"}}
            ])

        self.assertEqual(count, 1)
        self.assertEqual(service.embeddings.embed_documents.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(5), call(15)])

    def test_add_chunks_raises_after_three_rate_limit_retries(self):
        service = make_service(
            {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        )
        rate_limit_error = RuntimeError("RESOURCE_EXHAUSTED: 429")
        service.embeddings.embed_documents = Mock(side_effect=rate_limit_error)

        with (
            patch("app.services.rag_service.time.sleep") as sleep,
            self.assertRaisesRegex(RuntimeError, "RESOURCE_EXHAUSTED"),
        ):
            service.add_chunks([
                {"text": "chunk", "metadata": {"chunk_id": "chunk-1"}}
            ])

        self.assertEqual(service.embeddings.embed_documents.call_count, 4)
        self.assertEqual(sleep.call_args_list, [call(5), call(15), call(30)])

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
