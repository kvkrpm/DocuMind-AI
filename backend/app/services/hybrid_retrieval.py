import os
import re
import threading

from rank_bm25 import BM25Okapi


MODEL_NAME = "ms-marco-TinyBERT-L-2-v2"
RRF_K = 60
_ranker = None
_ranker_initialization_error = None
_ranker_lock = threading.Lock()


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", text.lower())


def rank_bm25(
    question: str,
    documents: list[dict],
    limit: int = 10,
) -> list[dict]:
    query_tokens = _tokenize(question)
    if not documents or not query_tokens:
        return []

    tokenized_documents = [_tokenize(document["text"]) for document in documents]
    bm25 = BM25Okapi(tokenized_documents)
    scores = bm25.get_scores(query_tokens)
    matching_indexes = [
        index
        for index, tokens in enumerate(tokenized_documents)
        if set(tokens).intersection(query_tokens)
    ]
    matching_indexes.sort(key=lambda index: (-scores[index], index))
    return [
        {**documents[index], "bm25_score": float(scores[index])}
        for index in matching_indexes[:limit]
    ]


def reciprocal_rank_fusion(
    dense_results: list[dict],
    bm25_results: list[dict],
    limit: int = 12,
) -> list[dict]:
    fused = {}
    for results in (dense_results, bm25_results):
        for rank, result in enumerate(results, start=1):
            candidate_id = str(result["id"])
            candidate = fused.setdefault(candidate_id, dict(result))
            candidate["rrf_score"] = candidate.get("rrf_score", 0.0) + (
                1.0 / (RRF_K + rank)
            )
            if "dense_score" not in candidate and "dense_score" in result:
                candidate["dense_score"] = result["dense_score"]
    return sorted(
        fused.values(),
        key=lambda candidate: (-candidate["rrf_score"], str(candidate["id"])),
    )[:limit]


def _ranker_cache_dir() -> str:
    deployed_cache = "/app/data/flashrank_cache"
    local_cache = "/tmp/flashrank_cache"
    running_in_container = (
        os.name == "posix"
        and os.path.abspath(os.getcwd()) == "/app"
    )
    cache_dir = deployed_cache if running_in_container else local_cache
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def get_ranker():
    global _ranker, _ranker_initialization_error
    if _ranker is None:
        with _ranker_lock:
            if _ranker is None:
                if _ranker_initialization_error is not None:
                    raise RuntimeError("FlashRank initialization previously failed") from (
                        _ranker_initialization_error
                    )
                from flashrank import Ranker

                try:
                    _ranker = Ranker(
                        model_name=MODEL_NAME,
                        cache_dir=_ranker_cache_dir(),
                    )
                except Exception as error:
                    _ranker_initialization_error = error
                    raise
    return _ranker


def rerank(question: str, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    from flashrank import RerankRequest

    passages = [
        {
            "id": str(candidate["id"]),
            "text": candidate["text"],
            "meta": candidate.get("metadata", {}),
        }
        for candidate in candidates
    ]
    ranked = get_ranker().rerank(
        RerankRequest(query=question, passages=passages)
    )
    candidates_by_id = {str(candidate["id"]): candidate for candidate in candidates}
    ordered = []
    for result in ranked:
        candidate = candidates_by_id.get(str(result.get("id")))
        if candidate is not None and candidate not in ordered:
            ordered.append(candidate)
    ordered_ids = {str(candidate["id"]) for candidate in ordered}
    ordered.extend(
        candidate
        for candidate in candidates
        if str(candidate["id"]) not in ordered_ids
    )
    return ordered
