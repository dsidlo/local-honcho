"""Simplified reranker client for cross-encoder re-ranking of retrieved documents."""

import asyncio
import logging
from typing import TYPE_CHECKING, NamedTuple

import httpx
import numpy as np

from src.config import settings

if TYPE_CHECKING:
    from src import models

logger = logging.getLogger(__name__)


class RerankedDocument(NamedTuple):
    """A document with its reranking score."""

    document: "models.Document"
    score: float


class _RerankerClient:
    """Client for cross-encoder reranking via Ollama or local models."""

    def __init__(self):
        self.enabled = settings.RERANKER.ENABLED
        self.provider = settings.RERANKER.PROVIDER
        self.model = settings.RERANKER.MODEL
        self.ollama_base_url = settings.RERANKER.OLLAMA_BASE_URL
        self.timeout = settings.RERANKER.TIMEOUT_SECONDS
        self.top_k = settings.RERANKER.TOP_K
        self.batch_size = settings.RERANKER.BATCH_SIZE

        if self.enabled:
            logger.info(
                f"[RERANKER-INIT] provider={self.provider}, model={self.model}, "
                f"enabled={self.enabled}, top_k={self.top_k}"
            )
        else:
            logger.debug("[RERANKER-INIT] Reranker disabled")

    async def rerank(
        self,
        query: str,
        documents: list,
        top_k: int | None = None,
    ) -> list:
        """
        Re-rank documents using cross-encoder.

        Args:
            query: The search query text
            documents: List of Document objects from RRF/hybrid search
            top_k: Number of top documents to return (defaults to settings.RERANKER.TOP_K)

        Returns:
            List of top_k documents, sorted by relevance score
        """
        if not self.enabled:
            logger.debug("[RERANKER] Skipping reranking (disabled)")
            return documents

        if not documents:
            return documents

        top_k = top_k or self.top_k

        if len(documents) <= top_k:
            # No need to rerank if we already have <= top_k documents
            logger.debug(f"[RERANKER] Skipping reranking ({len(documents)} <= {top_k})")
            return documents

        logger.debug(
            f"[RERANKER] Reranking {len(documents)} documents for query: {query[:50]}..."
        )

        try:
            if self.provider == "ollama":
                return await self._rerank_ollama(query, documents, top_k)
            else:
                logger.warning(f"[RERANKER] Unknown provider: {self.provider}")
                return documents[:top_k]
        except Exception as e:
            logger.error(f"[RERANKER] Error during reranking: {e}")
            # Fall back to original order on error
            return documents[:top_k]

    async def _rerank_ollama(
        self,
        query: str,
        documents: list,
        top_k: int,
    ) -> list:
        """
        Re-rank using Ollama API.

        Ollama reranker expects documents and query, returns relevance scores.
        """
        doc_contents = [doc.content for doc in documents]

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                # Try the rerank endpoint
                response = await client.post(
                    f"{self.ollama_base_url}/api/rerank",
                    json={
                        "model": self.model,
                        "query": query,
                        "documents": doc_contents,
                        "top_k": len(documents),  # Get scores for all
                    },
                )
                response.raise_for_status()
                result = response.json()

                # Extract scores from result
                if "results" in result:
                    # Format: { "results": [{"index": 0, "score": 0.9}, ...] }
                    scores = [0.0] * len(documents)
                    for item in result["results"]:
                        idx = item.get("index", 0)
                        scores[idx] = item.get("score", 0.0)
                elif "scores" in result:
                    # Format: { "scores": [0.9, 0.3, ...] }
                    scores = result["scores"]
                else:
                    logger.warning(f"[RERANKER] Unexpected response format: {result.keys()}")
                    return documents[:top_k]

            except httpx.HTTPStatusError as e:
                # If rerank endpoint doesn't exist, try embeddings endpoint
                logger.debug(f"[RERANKER] Rerank endpoint failed: {e}")
                return await self._rerank_via_embeddings(client, query, documents, top_k)

        # Sort documents by score and return top_k
        scored_docs = [
            RerankedDocument(doc=doc, score=float(score))
            for doc, score in zip(documents, scores)
        ]
        scored_docs.sort(key=lambda x: x.score, reverse=True)

        logger.debug(
            f"[RERANKER] Reranked {len(documents)} docs, "
            f"top scores: {[f'{d.score:.3f}' for d in scored_docs[:3]]}"
        )

        return [d.document for d in scored_docs[:top_k]]

    async def _rerank_via_embeddings(
        self,
        client: httpx.AsyncClient,
        query: str,
        documents: list,
        top_k: int,
    ) -> list:
        """
        Fallback: use embeddings similarity if rerank endpoint not available.

        This computes query embedding and document embeddings, then uses
        cosine similarity to score.
        """
        logger.debug("[RERANKER] Falling back to embeddings-based reranking")

        # Get query embedding
        query_response = await client.post(
            f"{self.ollama_base_url}/api/embeddings",
            json={"model": self.model, "prompt": query},
        )
        query_response.raise_for_status()
        query_embedding = query_response.json()["embedding"]

        # Get document embeddings (in batches)
        doc_embeddings = []
        for i in range(0, len(documents), self.batch_size):
            batch = documents[i : i + self.batch_size]
            batch_tasks = [
                client.post(
                    f"{self.ollama_base_url}/api/embeddings",
                    json={"model": self.model, "prompt": doc.content},
                )
                for doc in batch
            ]
            batch_responses = await asyncio.gather(*batch_tasks)
            for resp in batch_responses:
                resp.raise_for_status()
                doc_embeddings.append(resp.json()["embedding"])

        # Calculate cosine similarities
        query_vec = np.array(query_embedding)
        scores = []
        for doc_emb in doc_embeddings:
            doc_vec = np.array(doc_emb)
            similarity = np.dot(query_vec, doc_vec) / (
                np.linalg.norm(query_vec) * np.linalg.norm(doc_vec)
            )
            scores.append(float(similarity))

        # Sort and return top_k
        scored_docs = [
            RerankedDocument(doc=doc, score=score)
            for doc, score in zip(documents, scores)
        ]
        scored_docs.sort(key=lambda x: x.score, reverse=True)

        return [d.document for d in scored_docs[:top_k]]


# Global singleton instance
_reranker_instance: _RerankerClient | None = None


def get_reranker_client() -> _RerankerClient:
    """Get or create the global reranker client instance."""
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = _RerankerClient()
    return _reranker_instance


# Module-level convenience function
async def rerank_documents(
    query: str,
    documents: list,
    top_k: int | None = None,
) -> list:
    """
    Convenience function to rerank documents.

    Args:
        query: Search query text
        documents: List of documents to rerank
        top_k: Number of top documents to return

    Returns:
        Re-ranked list of documents
    """
    client = get_reranker_client()
    return await client.rerank(query, documents, top_k)
