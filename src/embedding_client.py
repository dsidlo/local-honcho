"""
Simplified embedding client focused on single-text embedding for Honcho.

This client embeds texts one at a time to avoid batching complexity and
Ollama/OpenAI API differences.
"""

import asyncio
import logging
import threading
from typing import Any, NamedTuple

import httpx
import tiktoken
from google import genai
from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger(__name__)

# Retry configuration for transient embed failures (e.g. Ollama 500s)
_EMBED_MAX_RETRIES: int = 3
_EMBED_RETRY_BASE_DELAY: float = 0.5  # seconds; doubles each retry


async def _embed_retry(fn, text_preview: str = "", *args, **kwargs):
    """Call an embedding function with exponential-backoff retry on transient errors.

    Retries on:
    - httpx.HTTPStatusError with 5xx status (server errors)
    - httpx.ConnectError / httpx.ReadError (network blips)
    - httpx.TimeoutException
    
    Args:
        fn: The async function to call
        text_preview: Preview of text being embedded (for debug logging)
        *args, **kwargs: Arguments to pass to fn
    """
    last_exc: Exception | None = None
    for attempt in range(1, _EMBED_MAX_RETRIES + 1):
        try:
            result = await fn(*args, **kwargs)
            if attempt > 1:
                logger.info(f"[EMBED-RETRY-SUCCESS] Succeeded on attempt {attempt}/{_EMBED_MAX_RETRIES} for text: {text_preview[:100]!r}...")
            return result
        except (
            httpx.HTTPStatusError,
            httpx.ConnectError,
            httpx.ReadError,
            httpx.TimeoutException,
        ) as exc:
            last_exc = exc
            # Only retry on server-side (5xx) HTTP errors, not 4xx
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                logger.error(
                    f"[EMBED-FAILURE] Client error (4xx), NOT retrying. "
                    f"Status: {exc.response.status_code}, "
                    f"Text preview: {text_preview[:200]!r}..."
                )
                raise
            
            # Log detailed error info
            error_details = f"{exc}"
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    response_body = exc.response.text[:500]
                    error_details = f"HTTP {exc.response.status_code}: {response_body}"
                except Exception:
                    pass
            
            if attempt < _EMBED_MAX_RETRIES:
                delay = _EMBED_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"[EMBED-RETRY] Attempt {attempt}/{_EMBED_MAX_RETRIES} failed: {error_details}. "
                    f"Text preview: {text_preview[:200]!r}... "
                    f"Text length: {len(text_preview)} chars. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            else:
                # All retries exhausted - log full context
                logger.error(
                    f"[EMBED-FINAL-FAILURE] All {_EMBED_MAX_RETRIES} attempts exhausted. "
                    f"Error: {error_details}. "
                    f"Text preview: {text_preview[:500]!r}, "
                    f"Text length: {len(text_preview)} chars"
                )
    # All retries exhausted
    raise last_exc  # type: ignore[misc]


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
        
        logger.debug(f"[EMBED-SINGLE] Starting embed: tokens={token_count}, length={len(query)}, preview={query[:200]!r}...")
        
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
            # Ollama embed API (with retry for transient 500s)
            async def _ollama_embed():
                logger.debug(f"[OLLAMA-SINGLE] Sending request: model={self.model}, text_length={len(query)}")
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
                    logger.error(f"[OLLAMA-SINGLE-EMPTY] No embeddings in response: {data}")
                    raise ValueError("No embedding returned from Ollama API")
                result = data["embeddings"][0] if isinstance(data["embeddings"][0], list) else data["embeddings"]
                
                # Check for NaN values
                import math
                if isinstance(result, list) and any(isinstance(x, float) and math.isnan(x) for x in result):
                    logger.error(f"[OLLAMA-SINGLE-NaN] Embedding contains NaN! query_preview={query[:300]!r}")
                    raise ValueError("Ollama returned NaN values in embedding")
                
                # Pad to 1536 dimensions for database compatibility
                if len(result) < 1536:
                    result = result + [0.0] * (1536 - len(result))
                logger.debug(f"[OLLAMA-SINGLE-SUCCESS] Returned embedding dim={len(result)}")
                return result

            return await _embed_retry(_ollama_embed, query)
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
        
        # Log text statistics
        total_chars = sum(len(t) for t in texts)
        avg_chars = total_chars / len(texts) if texts else 0
        logger.debug(f"[SIMPLE-BATCH] Text stats: total={len(texts)}, total_chars={total_chars}, avg_chars={avg_chars:.1f}, min={min(len(t) for t in texts) if texts else 0}, max={max(len(t) for t in texts) if texts else 0}")
        
        embeddings: list[list[float]] = []

        for i in range(0, len(texts), self.max_batch_size):
            batch = texts[i : i + self.max_batch_size]
            batch_start = i
            logger.debug(f"[SIMPLE-BATCH-BATCH] Processing batch {i//self.max_batch_size + 1}/{(len(texts) + self.max_batch_size - 1)//self.max_batch_size}, size={len(batch)}")
            
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
                    # Ollama: embed one at a time to avoid batching issues (with retry)
                    for batch_idx, text in enumerate(batch):
                        async def _ollama_batch_embed(_text=text, _idx=batch_idx, _start=batch_start):
                            logger.debug(f"[OLLAMA-BATCH] text_idx={_start + _idx}, length={len(_text)}, preview={_text[:150]!r}...")
                            response = await self.client.post(
                                f"{self.ollama_base_url}/api/embed",
                                json={
                                    "model": self.model,
                                    "input": _text,
                                },
                            )
                            response.raise_for_status()
                            data = response.json()
                            if "embeddings" not in data or not data["embeddings"]:
                                logger.error(f"[OLLAMA-BATCH-EMPTY] No embeddings in response for text_idx={_start + _idx}")
                                raise ValueError("No embedding returned from Ollama API")
                            embedding = data["embeddings"][0] if isinstance(data["embeddings"], list) else data["embeddings"]
                            
                            # Check for NaN values
                            import math
                            if isinstance(embedding, list) and any(isinstance(x, float) and math.isnan(x) for x in embedding):
                                logger.error(f"[OLLAMA-BATCH-NaN] NaN detected! text_idx={_start + _idx}, length={len(_text)}, preview={_text[:300]!r}...")
                                raise ValueError("Ollama returned NaN values in embedding")
                            
                            if len(embedding) < 1536:
                                embedding = embedding + [0.0] * (1536 - len(embedding))
                            logger.debug(f"[OLLAMA-BATCH-SUCCESS] text_idx={_start + _idx}, dim={len(embedding)}")
                            return embedding

                        embedding = await _embed_retry(_ollama_batch_embed, text)
                        embeddings.append(embedding)
                    
                else:  # openai
                    response = await self.client.embeddings.create(
                        input=batch,
                        model=self.model,
                    )
                    batch_embeddings = [data.embedding for data in response.data]
                    embeddings.extend(batch_embeddings)
                    
            except Exception as e:
                logger.error(f"[SIMPLE-BATCH-ERROR] Batch starting at index {batch_start} failed: {type(e).__name__}: {e}")
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

        logger.info(f"[BATCH-EMBED] Processing {len(id_resource_dict)} items ({self.provider}: {'sequential' if self.provider == 'ollama' else 'batched'})")

        # For Ollama, process all texts sequentially without batching
        results = {}
        for text_id, (text, tokens) in id_resource_dict.items():
            if len(tokens) > self.max_embedding_tokens:
                logger.warning(f"Text {text_id} exceeds token limit, truncating")
                # Truncate text to fit
                truncated_tokens = tokens[:self.max_embedding_tokens]
                text = self.encoding.decode(truncated_tokens)
            # Embed full (or truncated) text
            embedding = await self.embed(text)
            results[text_id] = [embedding]

        logger.info(f"[BATCH-EMBED] Done - returned embeddings for {len(results)} texts")
        return results

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
            # Ollama: no batching, process one at a time sequentially (with retry)
            results = {}
            for item in batch:
                async def _ollama_process_batch(_item=item):
                    logger.debug(f"[OLLAMA-EMBED-REQUEST] text_id={_item.text_id}, length={len(_item.text)}, preview={_item.text[:200]!r}...")
                    response = await self.client.post(
                        f"{self.ollama_base_url}/api/embed",
                        json={
                            "model": self.model,
                            "input": _item.text,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    if "embeddings" not in data or not data["embeddings"]:
                        logger.error(f"[OLLAMA-EMBED-EMPTY] No embeddings in response for text_id={_item.text_id}, response={data}")
                        raise ValueError("No embedding returned from Ollama API")
                    
                    embedding = data["embeddings"][0] if isinstance(data["embeddings"], list) else data["embeddings"]
                    
                    # Check for NaN values in embedding
                    if isinstance(embedding, list):
                        import math
                        has_nan = any(isinstance(x, float) and math.isnan(x) for x in embedding)
                        if has_nan:
                            logger.error(f"[OLLAMA-EMBED-NaN] Embedding contains NaN values! text_id={_item.text_id}, length={len(_item.text)}, preview={_item.text[:300]!r}...")
                            raise ValueError("Ollama returned NaN values in embedding")
                    
                    # Pad to 1536 dimensions
                    if len(embedding) < 1536:
                        embedding = embedding + [0.0] * (1536 - len(embedding))
                    
                    logger.debug(f"[OLLAMA-EMBED-SUCCESS] text_id={_item.text_id}, embedding_dim={len(embedding)}")
                    return embedding

                results[item.text_id] = await _embed_retry(_ollama_process_batch, _item.text)
            
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


class _EmbeddingClientProxy:
    """Proxy that always delegates to the current singleton instance.
    
    This ensures that when close_embedding_client() resets the singleton,
    code that imported embedding_client gets the new instance automatically.
    """
    
    def __getattr__(self, name: str) -> Any:
        return getattr(get_embedding_client(), name)


def get_embedding_client() -> _EmbeddingClient:
    """Get or create the singleton embedding client instance."""
    global _embedding_client_instance
    with _client_lock:
        if _embedding_client_instance is None:
            _embedding_client_instance = _EmbeddingClient()
        return _embedding_client_instance


# Module-level singleton instance for direct import (uses proxy)
embedding_client: _EmbeddingClient = _EmbeddingClientProxy()  # type: ignore[assignment]

# Backwards compatibility aliases
embedding = get_embedding_client


# Cleanup function for graceful shutdown
async def close_embedding_client() -> None:
    """Close the global embedding client instance."""
    global _embedding_client_instance
    with _client_lock:
        if _embedding_client_instance is not None:
            try:
                await _embedding_client_instance.close()
            except RuntimeError:
                # Event loop may be closed, ignore
                pass
            _embedding_client_instance = None