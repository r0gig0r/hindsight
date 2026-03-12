"""
Compare Infinity sidecar (TEI) reranker vs Jina MLX reranker.

Measures:
  - Cold start (model load) time
  - Per-query latency at different candidate counts (10, 50, 100, 300)
  - Ranking agreement (Kendall's tau, top-K overlap)

Usage:
  cd hindsight-api && uv run python -m benchmarks.perf.reranker_comparison
  # Or from repo root:
  cd hindsight-api && uv run python ../hindsight-dev/benchmarks/perf/reranker_comparison.py
"""

import asyncio
import json
import statistics
import time

import httpx

# --- Test data: realistic memory recall query + candidate passages ---
QUERIES = [
    "What programming languages does the user prefer?",
    "When did we last discuss the deployment architecture?",
    "What are the user's preferences for database systems?",
    "Tell me about the project timeline and deadlines",
    "How does the authentication system work?",
]


# 300 candidate documents of varying relevance (realistic for recall reranking)
def _generate_candidates(n: int) -> list[str]:
    """Generate n candidate passages with a mix of relevant and irrelevant content."""
    relevant = [
        "The user strongly prefers Python and Rust for backend development. They mentioned disliking Java.",
        "User said they use PostgreSQL for all projects and have experience with pgvector for embeddings.",
        "Last deployment discussion was on 2026-02-15 where we planned the Kubernetes migration.",
        "The auth system uses JWT tokens with refresh rotation, implemented in the FastAPI middleware.",
        "Project deadline is end of Q1 2026, with a soft launch planned for March 1st.",
        "User prefers functional programming patterns over OOP when possible.",
        "The database schema uses Alembic migrations with multi-tenant schema isolation.",
        "We discussed switching from Redis to PostgreSQL for session storage last week.",
        "The CI/CD pipeline runs on GitHub Actions with parallel test execution.",
        "User mentioned they want to add WebSocket support for real-time updates.",
        "The API uses async throughout with asyncpg for database connections.",
        "Deployment is on GKE with autoscaling based on CPU and memory metrics.",
        "The user has a strong preference for type hints and Pydantic models.",
        "Last architecture review covered the event-driven consolidation pipeline.",
        "Authentication supports both API keys and OAuth2 with PKCE flow.",
        "The team agreed on a 2-week sprint cycle with Friday demos.",
        "PostgreSQL with pgvector handles both structured data and vector similarity search.",
        "The monitoring stack uses Prometheus + Grafana with custom dashboards.",
        "User prefers minimal dependencies and avoids heavy frameworks when possible.",
        "The memory engine uses 4 parallel retrieval strategies: semantic, BM25, graph, temporal.",
    ]
    noise = [
        "The weather in Tokyo was pleasant during our virtual meeting.",
        "Coffee machine in the office needs maintenance again.",
        "Updated the README with new installation instructions.",
        "The cat sat on the mat and looked at the window.",
        "Remember to buy groceries: milk, eggs, bread, butter.",
        "The stock market showed mixed signals today with tech leading gains.",
        "New episode of the podcast about distributed systems was released.",
        "The neighbor's dog barks every morning at 6am without fail.",
        "Completed the annual security compliance audit successfully.",
        "The office plant needs watering every Tuesday and Friday.",
        "Highway traffic was particularly bad this morning due to construction.",
        "Ordered new monitors for the team, arriving next week.",
        "The quarterly report shows steady growth in active users.",
        "Lunch menu today: grilled chicken, pasta, vegetable soup.",
        "Backup script runs at 3am UTC and takes approximately 45 minutes.",
    ]
    candidates = []
    for i in range(n):
        if i < len(relevant):
            candidates.append(relevant[i])
        elif i < len(relevant) + len(noise):
            candidates.append(noise[i - len(relevant)])
        else:
            candidates.append(f"Irrelevant filler passage number {i} with some generic content about nothing.")
    return candidates


async def benchmark_infinity(query: str, documents: list[str], url: str = "http://127.0.0.1:7997") -> dict:
    """Benchmark the Infinity TEI reranker sidecar."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = {
            "query": query,
            "documents": documents,
            "model": "default",
        }
        start = time.perf_counter()
        resp = await client.post(f"{url}/rerank", json=payload)
        elapsed = time.perf_counter() - start
        resp.raise_for_status()
        data = resp.json()

    # Extract scores in original document order
    scores = [0.0] * len(documents)
    for result in data["results"]:
        scores[result["index"]] = result["relevance_score"]
    return {"scores": scores, "latency_ms": elapsed * 1000}


_jina_mlx_instance = None


async def benchmark_jina_mlx(query: str, documents: list[str]) -> dict:
    """Benchmark the Jina MLX reranker (in-process)."""
    global _jina_mlx_instance
    # Lazy import to avoid loading MLX unless needed
    from hindsight_api.engine.cross_encoder import JinaMLXCrossEncoder

    if _jina_mlx_instance is None:
        print("  [jina-mlx] Loading model (first call)...")
        load_start = time.perf_counter()
        _jina_mlx_instance = JinaMLXCrossEncoder()
        await _jina_mlx_instance.initialize()
        load_elapsed = time.perf_counter() - load_start
        print(f"  [jina-mlx] Model loaded in {load_elapsed:.1f}s")

    pairs = [(query, doc) for doc in documents]

    start = time.perf_counter()
    scores = await _jina_mlx_instance.predict(pairs)
    elapsed = time.perf_counter() - start

    return {"scores": scores, "latency_ms": elapsed * 1000}


def compute_ranking_agreement(scores_a: list[float], scores_b: list[float], top_k: int = 10) -> dict:
    """Compute ranking agreement metrics between two score lists."""
    n = len(scores_a)
    rank_a = sorted(range(n), key=lambda i: scores_a[i], reverse=True)
    rank_b = sorted(range(n), key=lambda i: scores_b[i], reverse=True)

    # Top-K overlap
    top_a = set(rank_a[:top_k])
    top_b = set(rank_b[:top_k])
    overlap = len(top_a & top_b)
    top_k_overlap = overlap / top_k

    # Kendall's tau (simplified: count concordant vs discordant pairs)
    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            a_order = scores_a[i] - scores_a[j]
            b_order = scores_b[i] - scores_b[j]
            if a_order * b_order > 0:
                concordant += 1
            elif a_order * b_order < 0:
                discordant += 1
    total = concordant + discordant
    tau = (concordant - discordant) / total if total > 0 else 0.0

    # Top-1 agreement
    top1_match = rank_a[0] == rank_b[0]

    return {
        f"top_{top_k}_overlap": top_k_overlap,
        "kendall_tau": tau,
        "top_1_match": top1_match,
        "top_a": rank_a[:top_k],
        "top_b": rank_b[:top_k],
    }


async def run_benchmark():
    print("=" * 70)
    print("RERANKER COMPARISON: Infinity (TEI) vs Jina MLX")
    print("=" * 70)

    candidate_counts = [10, 50, 100, 300]
    warmup_rounds = 2
    bench_rounds = 5

    all_results = {}

    for count in candidate_counts:
        print(f"\n--- {count} candidates ---")
        candidates = _generate_candidates(count)

        infinity_latencies = []
        jina_latencies = []
        agreements = []

        for query_idx, query in enumerate(QUERIES):
            # Warmup
            for _ in range(warmup_rounds):
                await benchmark_infinity(query, candidates)
                await benchmark_jina_mlx(query, candidates)

            # Benchmark rounds
            inf_times = []
            jina_times = []
            for _ in range(bench_rounds):
                inf_result = await benchmark_infinity(query, candidates)
                jina_result = await benchmark_jina_mlx(query, candidates)
                inf_times.append(inf_result["latency_ms"])
                jina_times.append(jina_result["latency_ms"])

            inf_median = statistics.median(inf_times)
            jina_median = statistics.median(jina_times)
            infinity_latencies.append(inf_median)
            jina_latencies.append(jina_median)

            # Quality comparison (use last round's scores)
            agreement = compute_ranking_agreement(inf_result["scores"], jina_result["scores"])
            agreements.append(agreement)

            print(
                f"  Q{query_idx + 1}: infinity={inf_median:.0f}ms  jina-mlx={jina_median:.0f}ms  "
                f"top10={agreement['top_10_overlap']:.0%}  tau={agreement['kendall_tau']:.3f}  "
                f"top1={'✓' if agreement['top_1_match'] else '✗'}"
            )

        avg_inf = statistics.mean(infinity_latencies)
        avg_jina = statistics.mean(jina_latencies)
        avg_overlap = statistics.mean(a["top_10_overlap"] for a in agreements)
        avg_tau = statistics.mean(a["kendall_tau"] for a in agreements)
        top1_pct = sum(1 for a in agreements if a["top_1_match"]) / len(agreements)

        all_results[count] = {
            "infinity_avg_ms": avg_inf,
            "jina_mlx_avg_ms": avg_jina,
            "speedup": avg_inf / avg_jina if avg_jina > 0 else float("inf"),
            "avg_top10_overlap": avg_overlap,
            "avg_kendall_tau": avg_tau,
            "top1_agreement": top1_pct,
        }

        print(
            f"  AVG: infinity={avg_inf:.0f}ms  jina-mlx={avg_jina:.0f}ms  "
            f"speedup={avg_inf / avg_jina:.2f}x  overlap={avg_overlap:.0%}  tau={avg_tau:.3f}"
        )

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(
        f"{'Candidates':>12} {'Infinity':>12} {'Jina MLX':>12} {'Speedup':>10} {'Top10 Ovlp':>12} {'Kendall τ':>10} {'Top1 Match':>12}"
    )
    print("-" * 82)
    for count, r in all_results.items():
        print(
            f"{count:>12} {r['infinity_avg_ms']:>10.0f}ms {r['jina_mlx_avg_ms']:>10.0f}ms "
            f"{r['speedup']:>9.2f}x {r['avg_top10_overlap']:>11.0%} {r['avg_kendall_tau']:>10.3f} {r['top1_agreement']:>11.0%}"
        )

    # Verdict
    print("\n--- VERDICT ---")
    r300 = all_results.get(300, all_results.get(100))
    if r300["avg_top10_overlap"] >= 0.7 and r300["jina_mlx_avg_ms"] <= r300["infinity_avg_ms"]:
        print("Jina MLX is VIABLE replacement: good quality + equal/better latency")
    elif r300["avg_top10_overlap"] >= 0.7:
        print(
            f"Jina MLX has good quality but is {r300['infinity_avg_ms'] / r300['jina_mlx_avg_ms']:.1f}x {'slower' if r300['jina_mlx_avg_ms'] > r300['infinity_avg_ms'] else 'faster'}"
        )
    else:
        print(f"Jina MLX has LOW quality agreement ({r300['avg_top10_overlap']:.0%} top-10 overlap) — keep Infinity")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
