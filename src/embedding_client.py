"""
Simplified embedding client focused on single-text embedding for Honcho.

This client embeds texts one at a time to avoid batching complexity and
Ollama/OpenAI API differences.
"""

import asyncio
import logging
import threading
from collections import defaultdict
from typing import NamedTuple

import httpx
import tiktoken
from google import genai
from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger(__name__)


class BatchItem(NamedTuple):
    """A single item in a batch with its metadata."""

    text: str
    text_id: str
    chunk_index: int


class _EmbeddingClient:
    """
    Embedding client supporting OpenAI and Gemini with chunking and batching support.
    """

    def __init__(self, api_key: str | None = None, provider: str | None = None):
        self.provider: str = provider or settings.LLM.EMBEDDING_PROVIDER
        logger.info(f"[EMBED-INIT] provider='{self.provider}'")

        if self.provider == "gemini":
            if api_key is None:
                api_key = settings.LLM.GEMINI_API_KEY
            if not api_key:
                raise ValueError("Gemini API key is required")
            self.client: genai.Client | AsyncOpenAI | httpx.AsyncClient = genai.Client(api_key=api_key)
            self.model: str = "gemini-embedding-001"
            # Gemini has a 2048 token limit
            self.max_embedding_tokens: int = min(settings.MAX_EMBEDDING_TOKENS, 2048)
            # Gemini batch size is not documented, using conservative estimate
            self.max_batch_size: int = 100
        elif self.provider == "openrouter":
            if api_key is None:
                api_key = settings.LLM.OPENAI_COMPATIBLE_API_KEY
            base_url = settings.LLM.OPENAI_COMPATIBLE_BASE_URL
            if not api_key:
                raise ValueError("OpenRouter API key is required")
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
            )
            self.model: str = "text-embedding-3-small"
            # Standard OpenAI limits
            self.max_embedding_tokens: int = settings.MAX_EMBEDDING_TOKENS
            self.max_batch_size: int = 2048
        elif self.provider == "ollama":
            self.client = httpx.AsyncClient(timeout=120.0)
            self.model: str = settings.LLM.OLLAMA_EMBEDDING_MODEL or "bge-m3"
            self.ollama_base_url: str = settings.LLM.OLLAMA_BASE_URL or "http://localhost:11434"
            # Ollama's sequence length varies by model, using conservative settings
            self.max_embedding_tokens: int = settings.MAX_EMBEDDING_TOKENS
            self.max_batch_size: int = 512  # Conservative limit
        else:  # openai
            if api_key is None:
                api_key = settings.LLM.OPENAI_API_KEY
            if not api_key:
                raise ValueError("OpenAI API key is required")
            self.client = AsyncOpenAI(api_key=api_key)
            self.model: str = "text-embedding-3-small"
            # Standard OpenAI limits
            self.max_embedding_tokens: int = settings.MAX_EMBEDDING_TOKENS
            self.max_batch_size: int = 2048

        # Tiktoken encoder for tokenization
        self.encoding = tiktoken.get_encoding("cl100k_base")

    async def close(self) -> None:
        """Close the client connection."""
        if hasattr(self, "client"):
            if isinstance(self.client, httpx.AsyncClient):
                await self.client.aclose()
            else:
                await self.client.close()

    async def embed(self, query: str) -> list[float]:
        """
        Embed a single query text for search purposes.

        Args:
            query: Text to embed

        Returns:
            Embedding vector
        """
        token_count = len(self.encoding.encode(query))
        
        if token_count > self.max_embedding_tokens:
            logger.error(f"[EMBED-ERROR] Token limit exceeded: {token_count} > {self.max_embedding_tokens}")
            raise ValueError(
                f"Query exceeds maximum token limit of {self.max_embedding_tokens} tokens (got {token_count} tokens)"
            )

        if isinstance(self.client, genai.Client):
            response = await self.client.aio.models.embed_content(
                model=self.model,
                contents=query,
                config={"output_dimensionality": 1536},
            )
            if not response.embeddings or not response.embeddings[0].values:
                raise ValueError("No embedding returned from Gemini API")
            return response.embeddings[0].values
        elif isinstance(self.client, httpx.AsyncClient):
            # Ollama embed API
            response = await self.client.post(
                f"{self.ollama_base_url}/api/embed",
                json={
                    "model": self.model,
                    "input": query,
                },
            )
            response.raise_for_status()
            data = response.json()
            if "embeddings" not in data or not data["embeddings"]:
                raise ValueError("No embedding returned from Ollama API")
            # Ollama returns list of embeddings for single input
            result = data["embeddings"][0] if isinstance(data["embeddings"][0], list) else data["embeddings"]
            # Pad to 1536 dimensions for database compatibility
            if len(result) < 1536:
                result = result + [0.0] * (1536 - len(result))
            return result
        else:  # openai
            response = await self.client.embeddings.create(
                model=self.model, input=query
            )
            return response.data[0].embedding

    async def simple_batch_embed(self, texts: list[str]) -> list[list[float]]:
        """
        Simple batch embedding for a list of text strings.

        Args:
            texts: List of text strings to embed

        Returns:
            List of embedding vectors corresponding to input texts

        Raises:
            ValueError: If any text exceeds token limits
        """
        logger.info(f"[SIMPLE-BATCH] Processing {len(texts)} texts with {self.provider}/{self.model}")
        
        embeddings: list[list[float]] = []

        for i in range(0, len(texts), self.max_batch_size):
            batch = texts[i : i + self.max_batch_size]
            
            try:
                if isinstance(self.client, genai.Client):
                    # Type cast needed due to genai type signature complexity
                    response = await self.client.aio.models.embed_content(
                        model=self.model,
                        contents=batch,  # pyright: ignore[reportArgumentType]
                        config={"output_dimensionality": 1536},
                    )
                    if response.embeddings:
                        for emb in response.embeddings:
                            if emb.values:
                                embeddings.append(emb.values)
                        
                elif isinstance(self.client, httpx.AsyncClient):
                    # Ollama: embed one at a time to avoid batching issues
                    for text in batch:
                        response = await self.client.post(
                            f"{self.ollama_base_url}/api/embed",
                            json={
                                "model": self.model,
                                "input": text,
                            },
                        )
                        response.raise_for_status()
                        data = response.json()
                        if "embeddings" not in data or not data["embeddings"]:
                            raise ValueError("No embedding returned from Ollama API")
                        # Ollama returns list even for single input
                        embedding = data["embeddings"][0] if isinstance(data["embeddings"], list) else data["embeddings"]
                        # Pad to 1536 dimensions for database compatibility
                        if len(embedding) < 1536:
                            embedding = embedding + [0.0] * (1536 - len(embedding))
                        embeddings.append(embedding)
                    
                else:  # openai
                    response = await self.client.embeddings.create(
                        input=batch,
                        model=self.model,
                    )
                    batch_embeddings = [data.embedding for data in response.data]
                    embeddings.extend(batch_embeddings)
                    
            except Exception as e:
                # Check if it's a token limit error and re-raise as ValueError for consistency
                if "token" in str(e).lower():
                    raise ValueError(
                        f"Text content exceeds maximum token limit of {self.max_embedding_tokens}."
                    ) from e
                raise

        logger.info(f"[SIMPLE-BATCH] Done - returned {len(embeddings)} embeddings")
        return embeddings

    async def batch_embed(
        self, id_resource_dict: dict[str, tuple[str, list[int]]]
    ) -> dict[str, list[list[float]]]:
        """
        Embed multiple texts, chunking long ones and batching API calls.

        Args:
            id_resource_dict: Maps text IDs to (text, encoded_tokens) tuples

        Returns:
            Maps text IDs to lists of embedding vectors (one per chunk)
        """
        if not id_resource_dict:
            return {}

        logger.info(f"[BATCH-EMBED] Processing {len(id_resource_dict)} items")

        # 1. Prepare chunks for all texts if needed
        text_chunks = self._prepare_chunks(id_resource_dict)
        total_chunks = sum(len(chunks) for chunks in text_chunks.values())
        
        # 2. Create batches that fit API limits (max 2048 embeddings per request, max 300,000 tokens per request)
        batches = self._create_batches(text_chunks)
        
        # 3. Process all batches concurrently
        batch_results = await asyncio.gather(
            *[self._process_batch(batch) for batch in batches],
        )
        
        # 4. Accumulate results preserving chunk order
        results: dict[str, list[list[float]]] = defaultdict(list)
        for batch in batch_results:
            for text_id, embedding in batch.items():
                results[text_id].append(embedding)

        logger.info(f"[BATCH-EMBED] Done - returned embeddings for {len(results)} texts")
        return dict(results)

    def _prepare_chunks(
        self, id_resource_dict: dict[str, tuple[str, list[int]]]
    ) -> dict[str, list[BatchItem]]:
        """
        Split texts into chunks if they exceed max token limit.
        
        Uses semantic chunking at paragraph boundaries where possible.
        """
        chunks: dict[str, list[BatchItem]] = {}
        
        for text_id, (text, tokens) in id_resource_dict.items():
            if len(tokens) <= self.max_embedding_tokens:
                # Text fits in single chunk
                chunks[text_id] = [BatchItem(text=text, text_id=text_id, chunk_index=0)]
            else:
                # Split into chunks using simple approach
                # For now use fixed-size chunks; semantic chunking can be added later
                chunk_size = self.max_embedding_tokens
                chunk_start = 0
                chunk_index = 0
                
                text_chunks = []
                while chunk_start < len(tokens):
                    chunk_end = min(chunk_start + chunk_size, len(tokens))
                    chunk_tokens = tokens[chunk_start:chunk_end]
                    # Decode chunk back to text
                    chunk_text = self.encoding.decode(chunk_tokens)
                    text_chunks.append(BatchItem(
                        text=chunk_text,
                        text_id=text_id,
                        chunk_index=chunk_index
                    ))
                    chunk_start = chunk_end
                    chunk_index += 1
                
                chunks[text_id] = text_chunks
                logger.info(f"[CHUNK] Split text {text_id} into {len(text_chunks)} chunks")
        
        return chunks

    def _create_batches(
        self, text_chunks: dict[str, list[BatchItem]]
    ) -> list[list[BatchItem]]:
        """
        Create batches of texts that fit within API limits.
        
        OpenAI limits:
        - max 2048 embeddings per request
        - max 300,000 tokens per request
        
        We batch conservatively to avoid hitting limits.
        """
        all_items: list[BatchItem] = []
        for text_id, chunks in text_chunks.items():
            all_items.extend(chunks)
        
        # Create batches respecting limits
        batches: list[list[BatchItem]] = []
        current_batch: list[BatchItem] = []
        current_token_count = 0
        
        for item in all_items:
            item_tokens = len(self.encoding.encode(item.text))
            
            # Check if adding this item would exceed limits
            would_exceed_batch_size = len(current_batch) >= self.max_batch_size
            would_exceed_tokens = current_token_count + item_tokens > 300_000
            
            if would_exceed_batch_size or would_exceed_tokens:
                if current_batch:
                    batches.append(current_batch)
                current_batch = [item]
                current_token_count = item_tokens
            else:
                current_batch.append(item)
                current_token_count += item_tokens
        
        # Don't forget the last batch
        if current_batch:
            batches.append(current_batch)
        
        return batches

    async def _process_batch(
        self, batch: list[BatchItem]
    ) -> dict[str, list[float]]:
        """
        Process a single batch of texts.
        
        Returns dict mapping text_id to embedding vector.
        For chunked texts, only the first chunk is embedded.
        """
        batch_texts = [item.text for item in batch]
        
        if isinstance(self.client, genai.Client):
            # Gemini batch embedding
            response = await self.client.aio.models.embed_content(
                model=self.model,
                contents=batch_texts,  # pyright: ignore[reportArgumentType]
                config={"output_dimensionality": 1536},
            )
            
            if not response.embeddings:
                raise ValueError("No embeddings returned from Gemini API")
            
            return {
                batch[i].text_id: response.embeddings[i].values
                for i in range(len(batch))
            }
        
        elif isinstance(self.client, httpx.AsyncClient):
            # Ollama: embed one at a time
            results = {}
            for item in batch:
                response = await self.client.post(
                    f"{self.ollama_base_url}/api/embed",
                    json={
                        "model": self.model,
                        "input": item.text,
                    },
                )
                response.raise_for_status()
                data = response.json()
                if "embeddings" not in data or not data["embeddings"]:
                    raise ValueError("No embedding returned from Ollama API")
                
                embedding = data["embeddings"][0] if isinstance(data["embeddings"], list) else data["embeddings"]
                # Pad to 1536 dimensions
                if len(embedding) < 1536:
                    embedding = embedding + [0.0] * (1536 - len(embedding))
                
                if isinstance(embedding, list):
                    results[item.text_id] = embedding
                else:
                    raise ValueError(f"Ollama returned unexpected embedding type: {type(embedding)}")
            
            return results
        
        else:  # openai
            # OpenAI batch embedding
            response = await self.client.embeddings.create(
                input=batch_texts,
                model=self.model,
            )
            
            return {
                batch[i].text_id: response.data[i].embedding
                for i in range(len(batch))
            }


# Global singleton instance - thread-safe
_embedding_client_instance: _EmbeddingClient | None = None
_client_lock = threading.Lock()


def get_embedding_client() -> _EmbeddingClient:
    """Get or create the singleton embedding client instance."""
    global _embedding_client_instance
    with _client_lock:
        if _embedding_client_instance is None:
            _embedding_client_instance = _EmbeddingClient()
        return _embedding_client_instance


# Module-level singleton instance for direct import
embedding_client = get_embedding_client()

# Backwards compatibility aliases
embedding = get_embedding_client


# Cleanup function for graceful shutdown
async def close_embedding_client() -> None:
    """Close the global embedding client instance."""
    global _embedding_client_instance
    with _client_lock:
        if _embedding_client_instance is not None:
            await _embedding_client_instance.close()
            _embedding_client_instance = None