# Offline RAG Retrieval Benchmark

Cases: 12 | Cutoff: top 4

| Pipeline | Recall@4 (Hit Rate) | MRR@4 | Avg latency (ms/query) |
|---|---:|---:|---:|
| Dense Vector Only | 0.917 | 0.292 | 0.022 |
| Hybrid (BM25 + Vector RRF) | 0.917 | 0.708 | 2.362 |
| Hybrid + TinyBERT Reranker | 1.000 | 1.000 | 12.054 |

TinyBERT reranker ran with the local FlashRank model.

Recall@4 is reported as hit rate: the fraction of questions with at least one relevant chunk in the first four results. MRR@4 is the mean reciprocal rank of the first relevant chunk, with misses scored as zero.

This is an API-free benchmark. Dense results are fixed rankings in `sample_dataset.json`, not live Gemini/Chroma measurements; latency covers local ranking only and excludes embedding and answer-generation calls. FlashRank is loaded once and kept out of per-query latency via warm-up. For air-gapped runs, pre-populate the configured FlashRank cache; an uncached model is downloaded on first use.
