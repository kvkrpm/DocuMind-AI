import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.hybrid_retrieval import (  # noqa: E402
    get_ranker,
    rank_bm25,
    reciprocal_rank_fusion,
    rerank,
)


logger = logging.getLogger("rag_eval")
TOP_K = 4
DENSE_TOP_K = 10
BM25_TOP_K = 10
RRF_CANDIDATE_LIMIT = 12


def _validate_dataset(dataset: dict) -> None:
    cases = dataset.get("cases", [])
    chunks = dataset.get("chunks", [])
    if not 12 <= len(cases) <= 15:
        raise ValueError("The evaluation dataset must contain 12 to 15 cases")
    chunk_ids = {str(chunk["id"]) for chunk in chunks}
    for case in cases:
        if not case.get("relevant_chunk_ids"):
            raise ValueError("Every case must have at least one relevant chunk")
        referenced_ids = (
            set(case["relevant_chunk_ids"]) | set(case["dense_ranked_ids"])
        )
        if not referenced_ids.issubset(chunk_ids):
            raise ValueError(
                f"Case references unknown chunks: {referenced_ids - chunk_ids}"
            )


def _metrics(ranked_ids: list[str], relevant_ids: set[str]) -> tuple[float, float]:
    top_ids = ranked_ids[:TOP_K]
    relevant_in_top = [rank for rank, item in enumerate(top_ids, 1) if item in relevant_ids]
    hit_rate = 1.0 if relevant_in_top else 0.0
    reciprocal_rank = 1.0 / relevant_in_top[0] if relevant_in_top else 0.0
    return hit_rate, reciprocal_rank


def _dense_candidates(case: dict, chunks_by_id: dict[str, dict]) -> list[dict]:
    return [
        {
            "id": chunk_id,
            "text": chunks_by_id[chunk_id]["text"],
            "metadata": chunks_by_id[chunk_id]["metadata"],
            "dense_score": float(len(case["dense_ranked_ids"]) - rank),
        }
        for rank, chunk_id in enumerate(case["dense_ranked_ids"][:DENSE_TOP_K])
    ]


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def evaluate(dataset: dict) -> tuple[str, bool]:
    _validate_dataset(dataset)
    chunks = [
        {
            "id": str(chunk["id"]),
            "text": chunk["text"],
            "metadata": {
                "document_name": chunk["document_name"],
                "page_number": chunk["page_number"],
            },
        }
        for chunk in dataset["chunks"]
    ]
    chunks_by_id = {chunk["id"]: chunk for chunk in chunks}
    pipeline_results = {
        "Dense Vector Only": {"hits": [], "mrr": [], "latency": []},
        "Hybrid (BM25 + Vector RRF)": {"hits": [], "mrr": [], "latency": []},
        "Hybrid + TinyBERT Reranker": {"hits": [], "mrr": [], "latency": []},
    }

    ranker_available = True
    reranker_successes = 0
    try:
        get_ranker()
        if chunks:
            rerank("evaluation warm-up", chunks[:1])
    except Exception:
        ranker_available = False
        logger.exception("FlashRank is unavailable; recording RRF fallback results")

    for case in dataset["cases"]:
        dense_start = time.perf_counter()
        dense_candidates = _dense_candidates(case, chunks_by_id)
        dense_latency = (time.perf_counter() - dense_start) * 1000

        hybrid_start = time.perf_counter()
        bm25_candidates = rank_bm25(
            case["question"],
            chunks,
            limit=BM25_TOP_K,
        )
        fused_candidates = reciprocal_rank_fusion(
            dense_candidates,
            bm25_candidates,
            limit=RRF_CANDIDATE_LIMIT,
        )
        hybrid_latency = dense_latency + (
            time.perf_counter() - hybrid_start
        ) * 1000
        relevant_ids = set(case["relevant_chunk_ids"])

        rerank_start = time.perf_counter()
        if ranker_available:
            try:
                reranked_candidates = rerank(case["question"], fused_candidates)
                if not reranked_candidates:
                    reranked_candidates = fused_candidates
                else:
                    reranker_successes += 1
            except Exception:
                logger.exception("FlashRank failed for one case; using RRF ranking")
                reranked_candidates = fused_candidates
        else:
            reranked_candidates = fused_candidates
        rerank_latency = hybrid_latency + (
            time.perf_counter() - rerank_start
        ) * 1000

        for name, candidates, latency in (
            ("Dense Vector Only", dense_candidates[:TOP_K], dense_latency),
            (
                "Hybrid (BM25 + Vector RRF)",
                fused_candidates[:TOP_K],
                hybrid_latency,
            ),
            (
                "Hybrid + TinyBERT Reranker",
                reranked_candidates[:TOP_K],
                rerank_latency,
            ),
        ):
            hit_rate, reciprocal_rank = _metrics(
                [str(candidate["id"]) for candidate in candidates],
                relevant_ids,
            )
            metrics = pipeline_results[name]
            metrics["hits"].append(hit_rate)
            metrics["mrr"].append(reciprocal_rank)
            metrics["latency"].append(latency)

    if reranker_successes == len(dataset["cases"]):
        status = "TinyBERT reranker ran with the local FlashRank model."
    elif reranker_successes:
        status = (
            f"TinyBERT reranking succeeded for {reranker_successes} of "
            f"{len(dataset['cases'])} cases; remaining cases used RRF fallback."
        )
    else:
        status = "TinyBERT was unavailable; that row uses the RRF fallback."
    lines = [
        "# Offline RAG Retrieval Benchmark",
        "",
        f"Cases: {len(dataset['cases'])} | Cutoff: top {TOP_K}",
        "",
        "| Pipeline | Recall@4 (Hit Rate) | MRR@4 | Avg latency (ms/query) |",
        "|---|---:|---:|---:|",
    ]
    for name, metrics in pipeline_results.items():
        lines.append(
            f"| {name} | {_average(metrics['hits']):.3f} "
            f"| {_average(metrics['mrr']):.3f} "
            f"| {_average(metrics['latency']):.3f} |"
        )
    lines.extend([
        "",
        status,
        "",
        "Recall@4 is reported as hit rate: the fraction of questions with at least "
        "one relevant chunk in the first four results. MRR@4 is the mean reciprocal "
        "rank of the first relevant chunk, with misses scored as zero.",
        "",
        "This is an API-free benchmark. Dense results are fixed rankings in "
        "`sample_dataset.json`, not live Gemini/Chroma measurements; latency covers "
        "local ranking only and excludes embedding and answer-generation calls. "
        "FlashRank is loaded once and kept out of per-query latency via warm-up. "
        "For air-gapped runs, pre-populate the configured FlashRank cache; an "
        "uncached model is downloaded on first use.",
        "",
    ])
    return "\n".join(lines), ranker_available


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline hybrid RAG evaluation")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("sample_dataset.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("BENCHMARK.md"),
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report, _ = evaluate(dataset)
    args.output.write_text(report, encoding="utf-8")
    print(report, end="")
    print(f"Saved benchmark to {args.output}")


if __name__ == "__main__":
    main()
