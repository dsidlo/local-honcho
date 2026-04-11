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

# ---------------------------------------------------------------------------
# Known embedding model context windows (tokens)
# ---------------------------------------------------------------------------
# Used as fallback when the provider doesn't expose context_length dynamically.
# Keys are case-insensitive model name substrings matched against the model ID.
_EMBEDDING_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # Ollama / GGUF models
    "qwen3-embedding": 40_960,  # Ollama reports 40960; HF spec says 32K
    "qwen2.5-embedding": 32_768,
    "bge-m3": 8192,
    "nomic-embed-text": 8192,
    "mxbai-embed-large": 512,
    "all-minilm": 512,
    # OpenAI
    "text-embedding-3-small": 8191,
    "text-embedding-3-large": 8191,
    "text-embedding-ada-002": 8191,
    # Gemini
    "gemini-embedding-001": 2048,
    "text-embedding-004": 2048,
}


def _lookup_model_context_window(model: str) -> int | None:
    """Look up a model's max context window from the static registry.

    Performs case-insensitive substring matching against known model names.
    Returns the token limit or None if unknown.
    """
    model_lower = model.lower()
    for key, limit in _EMBEDDING_MODEL_CONTEXT_WINDOWS.items():
        if key in model_lower:
            return limit
    return None


async def _query_ollama_context_length(
    model: str, base_url: str, client: httpx.AsyncClient
) -> int | None:
    """Query the Ollama /api/show endpoint for a model's context_length.

    Ollama returns model_info fields like "qwen3.context_length" or
    "llama.context_length".  We search for any key ending in
    "context_length" and return the largest integer value found.

    Returns None if the query fails or no context_length is found.
    """
    logger.debug(
        f"[EMBED-CTX] Querying Ollama /api/show for model='{model}', "
        f"base_url='{base_url}'"
    )
    try:
        resp = await client.post(
            f"{base_url}/api/show",
            json={"name": model},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        model_info = data.get("model_info", {})
        logger.debug(
            f"[EMBED-CTX] Ollama /api/show response keys for '{model}': "
            f"{[k for k in model_info if 'context' in k.lower()]}"
        )
        # Find any key ending in context_length (e.g. qwen3.context_length)
        context_lengths = [
            v for k, v in model_info.items()
            if k.endswith("context_length") and isinstance(v, int)
        ]
        if context_lengths:
            result = max(context_lengths)
            logger.debug(
                f"[EMBED-CTX] Ollama context_length candidates for '{model}': "
                f"{context_lengths}, using max={result}"
            )
            return result
        else:
            logger.warning(
                f"[EMBED-CTX] No context_length found in Ollama model_info "
                f"for model='{model}'. Available keys: "
                f"{list(model_info.keys())[:20]}"
            )
            return None
    except httpx.HTTPStatusError as exc:
        logger.warning(
            f"[EMBED-CTX] Ollama /api/show HTTP error for model='{model}': "
            f"status={exc.response.status_code}, body={exc.response.text[:200]}"
        )
        return None
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        logger.warning(
            f"[EMBED-CTX] Ollama /api/show connection error for model='{model}': "
            f"{type(exc).__name__}: {exc}"
        )
        return None
    except Exception as exc:
        logger.warning(
            f"[EMBED-CTX] Failed to query Ollama context_length for "
            f"model='{model}': {type(exc).__name__}: {exc}"
        )
        return None

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

    The ``max_embedding_tokens`` attribute is resolved at init time from (in order):
    1. The provider's own API when it exposes model metadata (Ollama /api/show)
    2. A static registry of well-known model context windows
    3. The ``MAX_EMBEDDING_TOKENS`` config setting as a hard upper-bound cap

    This ensures the per-chunk token limit never exceeds what the actual
    embedding model can handle.
    """

    # Will be set to True once the async context-length probe completes
    _context_length_resolved: bool = False

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
            # Gemini context window is known: 2048 tokens – this is a hard cap
            self.max_embedding_tokens: int = min(settings.MAX_EMBEDDING_TOKENS, 2048)
            self.max_batch_size: int = 100
            logger.debug(
                f"[EMBED-INIT] Gemini branch: model='{self.model}', "
                f"max_tokens={self.max_embedding_tokens}, "
                f"batch_size={self.max_batch_size}"
            )
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
            # Resolve from registry, capped by config
            model_ctx = _lookup_model_context_window(self.model) or 8191
            self.max_embedding_tokens: int = min(settings.MAX_EMBEDDING_TOKENS, model_ctx)
            self.max_batch_size: int = 2048
            logger.debug(
                f"[EMBED-INIT] OpenRouter branch: model='{self.model}', "
                f"base_url='{base_url}', registry_ctx={model_ctx}, "
                f"max_tokens={self.max_embedding_tokens}, batch_size={self.max_batch_size}"
            )
        elif self.provider == "ollama":
            self.client = httpx.AsyncClient(timeout=120.0)
            self.model: str = settings.LLM.OLLAMA_EMBEDDING_MODEL or "bge-m3"
            self.ollama_base_url: str = settings.LLM.OLLAMA_BASE_URL or "http://localhost:11434"
            # Placeholder – will be resolved asynchronously via _resolve_context_length()
            self.max_embedding_tokens: int = settings.MAX_EMBEDDING_TOKENS
            self.max_batch_size: int = 512  # Conservative limit
            # Use the OpenAI-compatible endpoint which supports Matryoshka dimensions
            # for models like qwen3-embedding that support variable output dims
            self.embed_dimensions: int = 1536
            # Schedule async context-length resolution on first use
            self._context_length_resolved: bool = False
            logger.debug(
                f"[EMBED-INIT] Ollama branch: model='{self.model}', "
                f"base_url='{self.ollama_base_url}', max_tokens={self.max_embedding_tokens} (pending resolve), "
                f"batch_size={self.max_batch_size}, embed_dims={self.embed_dimensions}"
            )
        else:  # openai
            if api_key is None:
                api_key = settings.LLM.OPENAI_API_KEY
            if not api_key:
                raise ValueError("OpenAI API key is required")
            self.client = AsyncOpenAI(api_key=api_key)
            self.model: str = "text-embedding-3-small"
            # Resolve from registry, capped by config
            model_ctx = _lookup_model_context_window(self.model) or 8191
            self.max_embedding_tokens: int = min(settings.MAX_EMBEDDING_TOKENS, model_ctx)
            self.max_batch_size: int = 2048
            logger.debug(
                f"[EMBED-INIT] OpenAI branch: model='{self.model}', "
                f"registry_ctx={model_ctx}, max_tokens={self.max_embedding_tokens}, "
                f"batch_size={self.max_batch_size}"
            )

        # Tiktoken encoder for tokenization
        self.encoding = tiktoken.get_encoding("cl100k_base")

        logger.info(
            f"[EMBED-INIT] max_embedding_tokens={self.max_embedding_tokens} "
            f"(config={settings.MAX_EMBEDDING_TOKENS}, "
            f"model={self.model}, provider={self.provider})"
        )

    async def _resolve_context_length(self) -> None:
        """Resolve the model's actual context window for Ollama providers.

        Queries the Ollama /api/show endpoint first, then falls back to the
        static registry, and finally to the config value.  The result is
        capped by ``settings.MAX_EMBEDDING_TOKENS`` so the admin can always
        set a hard upper bound.
        """
        if self._context_length_resolved or self.provider != "ollama":
            logger.debug(
                f"[EMBED-CTX] Skipping context resolution: "
                f"resolved={self._context_length_resolved}, "
                f"provider={self.provider}"
            )
            return

        logger.debug(
            f"[EMBED-CTX] Resolving context length for model='{self.model}', "
            f"provider='{self.provider}', "
            f"current_max={self.max_embedding_tokens}"
        )

        model_ctx: int | None = None
        resolution_source: str = "config_default"

        # 1. Try the Ollama API
        if isinstance(self.client, httpx.AsyncClient):
            model_ctx = await _query_ollama_context_length(
                self.model, self.ollama_base_url, self.client
            )
            if model_ctx is not None:
                resolution_source = "ollama_api"
                logger.info(
                    f"[EMBED-CTX] Resolved from Ollama API: context_length={model_ctx} "
                    f"for model='{self.model}'"
                )
            else:
                logger.debug(
                    f"[EMBED-CTX] Ollama API returned no context_length for '{self.model}', "
                    f"falling back to registry"
                )

        # 2. Fall back to static registry
        if model_ctx is None:
            model_ctx = _lookup_model_context_window(self.model)
            if model_ctx is not None:
                resolution_source = "static_registry"
                logger.info(
                    f"[EMBED-CTX] Resolved from registry: context_length={model_ctx} "
                    f"for model='{self.model}'"
                )
            else:
                logger.warning(
                    f"[EMBED-CTX] Model '{self.model}' not found in registry, "
                    f"using config default={self.max_embedding_tokens}"
                )

        # 3. Apply resolved value, capped by config
        if model_ctx is not None:
            old_val = self.max_embedding_tokens
            self.max_embedding_tokens = min(settings.MAX_EMBEDDING_TOKENS, model_ctx)
            if self.max_embedding_tokens != old_val:
                logger.debug(
                    f"[EMBED-CTX] Updated max_embedding_tokens: "
                    f"{old_val} -> {self.max_embedding_tokens} "
                    f"(min of config={settings.MAX_EMBEDDING_TOKENS}, "
                    f"model_ctx={model_ctx})"
                )
            resolution_source_actual = resolution_source
        else:
            resolution_source_actual = "config_default"

        logger.info(
            f"[EMBED-CTX] Final max_embedding_tokens={self.max_embedding_tokens} "
            f"for model='{self.model}' (source={resolution_source_actual}, "
            f"config_cap={settings.MAX_EMBEDDING_TOKENS})"
        )
        self._context_length_resolved = True

    async def close(self) -> None:
        """Close the client connection."""
        logger.debug(f"[EMBED-CLOSE] Closing embedding client (provider={getattr(self, 'provider', 'unknown')})")
        if hasattr(self, "client"):
            if isinstance(self.client, httpx.AsyncClient):
                await self.client.aclose()
                logger.debug("[EMBED-CLOSE] httpx client closed")
            else:
                await self.client.close()
                logger.debug("[EMBED-CLOSE] client closed")

    async def embed(self, query: str) -> list[float]:
        """
        Embed a single query text for search purposes.

        Args:
            query: Text to embed

        Returns:
            Embedding vector
        """
        await self._resolve_context_length()
        token_count = len(self.encoding.encode(query))
        
        logger.debug(
            f"[EMBED-SINGLE] Entry: provider={self.provider}, model={self.model}, "
            f"tokens={token_count}/{self.max_embedding_tokens}, "
            f"length={len(query)}, preview={query[:100]!r}..."
        )
        
        if token_count > self.max_embedding_tokens:
            logger.error(
                f"[EMBED-SINGLE] Token limit exceeded: {token_count} > {self.max_embedding_tokens}, "
                f"preview={query[:200]!r}..."
            )
            raise ValueError(
                f"Query exceeds maximum token limit of {self.max_embedding_tokens} tokens (got {token_count} tokens)"
            )

        if isinstance(self.client, genai.Client):
            logger.debug("[EMBED-SINGLE] Using Gemini provider")
            response = await self.client.aio.models.embed_content(
                model=self.model,
                contents=query,
                config={"output_dimensionality": 1536},
            )
            if not response.embeddings or not response.embeddings[0].values:
                logger.error("[EMBED-SINGLE] No embedding returned from Gemini API")
                raise ValueError("No embedding returned from Gemini API")
            result = response.embeddings[0].values
            logger.debug(f"[EMBED-SINGLE] Gemini success: dim={len(result)}")
            return result
        elif isinstance(self.client, httpx.AsyncClient):
            # Ollama embed via OpenAI-compatible endpoint (supports Matryoshka dimensions)
            async def _ollama_embed():
                logger.debug(f"[OLLAMA-SINGLE] Sending request: model={self.model}, text_length={len(query)}")
                response = await self.client.post(
                    f"{self.ollama_base_url}/v1/embeddings",
                    json={
                        "model": self.model,
                        "input": query,
                        "dimensions": self.embed_dimensions,
                    },
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                data = response.json()
                if "data" not in data or not data["data"] or "embedding" not in data["data"][0]:
                    logger.error(f"[OLLAMA-SINGLE-EMPTY] No embeddings in response: {data}")
                    raise ValueError("No embedding returned from Ollama API")
                result = [float(x) for x in data["data"][0]["embedding"]]
                
                # Check for NaN values
                import math
                if any(math.isnan(x) for x in result):
                    logger.error(f"[OLLAMA-SINGLE-NaN] Embedding contains NaN! query_preview={query[:300]!r}")
                    raise ValueError("Ollama returned NaN values in embedding")
                
                # Pad to target dimensions for database compatibility
                if len(result) < self.embed_dimensions:
                    result = result + [0.0] * (self.embed_dimensions - len(result))
                logger.debug(f"[OLLAMA-SINGLE-SUCCESS] Returned embedding dim={len(result)}")
                return result

            return await _embed_retry(_ollama_embed, query)
        else:  # openai
            logger.debug("[EMBED-SINGLE] Using OpenAI provider")
            response = await self.client.embeddings.create(
                model=self.model, input=query
            )
            result = response.data[0].embedding
            logger.debug(f"[EMBED-SINGLE] OpenAI success: dim={len(result)}")
            return result

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
        await self._resolve_context_length()
        logger.info(
            f"[SIMPLE-BATCH] Entry: {len(texts)} texts, "
            f"provider={self.provider}, model={self.model}, "
            f"max_tokens={self.max_embedding_tokens}"
        )
        
        # Log text statistics
        total_chars = sum(len(t) for t in texts)
        avg_chars = total_chars / len(texts) if texts else 0
        min_chars = min(len(t) for t in texts) if texts else 0
        max_chars = max(len(t) for t in texts) if texts else 0
        logger.debug(
            f"[SIMPLE-BATCH] Text stats: count={len(texts)}, "
            f"total_chars={total_chars}, avg={avg_chars:.0f}, "
            f"min={min_chars}, max={max_chars}"
        )
        
        embeddings: list[list[float]] = []

        for i in range(0, len(texts), self.max_batch_size):
            batch = texts[i : i + self.max_batch_size]
            batch_start = i
            logger.debug(f"[SIMPLE-BATCH-BATCH] Processing batch {i//self.max_batch_size + 1}/{(len(texts) + self.max_batch_size - 1)//self.max_batch_size}, size={len(batch)}")
            
            try:
                logger.debug(
                    f"[SIMPLE-BATCH] Batch {i//self.max_batch_size + 1}/"
                    f"{(len(texts) + self.max_batch_size - 1)//self.max_batch_size}: "
                    f"batch_size={len(batch)}, provider={self.provider}"
                )
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
                    logger.debug(f"[SIMPLE-BATCH] Ollama sequential: {len(batch)} texts in batch")
                    # Ollama: embed one at a time via OpenAI-compatible endpoint (with retry)
                    for batch_idx, text in enumerate(batch):
                        async def _ollama_batch_embed(_text=text, _idx=batch_idx, _start=batch_start):
                            logger.debug(f"[OLLAMA-BATCH] text_idx={_start + _idx}, length={len(_text)}, preview={_text[:150]!r}...")
                            response = await self.client.post(
                                f"{self.ollama_base_url}/v1/embeddings",
                                json={
                                    "model": self.model,
                                    "input": _text,
                                    "dimensions": self.embed_dimensions,
                                },
                                headers={"Content-Type": "application/json"},
                            )
                            response.raise_for_status()
                            data = response.json()
                            if "data" not in data or not data["data"] or "embedding" not in data["data"][0]:
                                logger.error(f"[OLLAMA-BATCH-EMPTY] No embeddings in response for text_idx={_start + _idx}")
                                raise ValueError("No embedding returned from Ollama API")
                            embedding = [float(x) for x in data["data"][0]["embedding"]]
                            
                            # Check for NaN values
                            import math
                            if any(math.isnan(x) for x in embedding):
                                logger.error(f"[OLLAMA-BATCH-NaN] NaN detected! text_idx={_start + _idx}, length={len(_text)}, preview={_text[:300]!r}...")
                                raise ValueError("Ollama returned NaN values in embedding")
                            
                            if len(embedding) < self.embed_dimensions:
                                embedding = embedding + [0.0] * (self.embed_dimensions - len(embedding))
                            logger.debug(f"[OLLAMA-BATCH-SUCCESS] text_idx={_start + _idx}, dim={len(embedding)}")
                            return embedding

                        embedding = await _embed_retry(_ollama_batch_embed, text)
                        embeddings.append(embedding)
                    
                else:  # openai
                    logger.debug(f"[SIMPLE-BATCH] OpenAI batch: {len(batch)} texts")
                    response = await self.client.embeddings.create(
                        input=batch,
                        model=self.model,
                    )
                    batch_embeddings = [data.embedding for data in response.data]
                    embeddings.extend(batch_embeddings)
                    
            except Exception as e:
                logger.error(
                    f"[SIMPLE-BATCH-ERROR] Batch starting at index {batch_start} failed: "
                    f"{type(e).__name__}: {e}, provider={self.provider}, "
                    f"batch_size={len(batch)}, texts_so_far={len(embeddings)}"
                )
                # Check if it's a token limit error and re-raise as ValueError for consistency
                if "token" in str(e).lower():
                    raise ValueError(
                        f"Text content exceeds maximum token limit of {self.max_embedding_tokens}."
                    ) from e
                raise

        logger.info(
            f"[SIMPLE-BATCH] Complete: {len(embeddings)} embeddings returned "
            f"from {len(texts)} input texts, provider={self.provider}"
        )
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
        await self._resolve_context_length()
        if not id_resource_dict:
            logger.debug("[BATCH-EMBED] Empty input, returning empty dict")
            return {}

        logger.info(
            f"[BATCH-EMBED] Entry: {len(id_resource_dict)} items, "
            f"provider={self.provider}, model={self.model}, "
            f"mode={'sequential' if self.provider == 'ollama' else 'batched'}"
        )

        # For Ollama, process all texts sequentially without batching
        results = {}
        for text_id, (text, tokens) in id_resource_dict.items():
            if len(tokens) > self.max_embedding_tokens:
                logger.warning(
                    f"[BATCH-EMBED] Text '{text_id}' exceeds token limit "
                    f"({len(tokens)} > {self.max_embedding_tokens}), truncating"
                )
                # Truncate text to fit
                truncated_tokens = tokens[:self.max_embedding_tokens]
                text = self.encoding.decode(truncated_tokens)
            # Embed full (or truncated) text
            embedding = await self.embed(text)
            results[text_id] = [embedding]

        logger.info(
            f"[BATCH-EMBED] Complete: {len(results)} texts embedded, "
            f"provider={self.provider}"
        )
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
            # Ollama: no batching, process one at a time via OpenAI-compatible endpoint (with retry)
            results = {}
            for item in batch:
                async def _ollama_process_batch(_item=item):
                    logger.debug(f"[OLLAMA-EMBED-REQUEST] text_id={_item.text_id}, length={len(_item.text)}, preview={_item.text[:200]!r}...")
                    response = await self.client.post(
                        f"{self.ollama_base_url}/v1/embeddings",
                        json={
                            "model": self.model,
                            "input": _item.text,
                            "dimensions": self.embed_dimensions,
                        },
                        headers={"Content-Type": "application/json"},
                    )
                    response.raise_for_status()
                    data = response.json()
                    if "data" not in data or not data["data"] or "embedding" not in data["data"][0]:
                        logger.error(f"[OLLAMA-EMBED-EMPTY] No embeddings in response for text_id={_item.text_id}, response={data}")
                        raise ValueError("No embedding returned from Ollama API")
                    
                    embedding = [float(x) for x in data["data"][0]["embedding"]]
                    
                    # Check for NaN values in embedding
                    import math
                    has_nan = any(math.isnan(x) for x in embedding)
                    if has_nan:
                        logger.error(f"[OLLAMA-EMBED-NaN] Embedding contains NaN values! text_id={_item.text_id}, length={len(_item.text)}, preview={_item.text[:300]!r}...")
                        raise ValueError("Ollama returned NaN values in embedding")
                    
                    # Pad to target dimensions
                    if len(embedding) < self.embed_dimensions:
                        embedding = embedding + [0.0] * (self.embed_dimensions - len(embedding))
                    
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
        logger.debug(
            f"[EMBED-SHUTDOWN] Closing embedding client, "
            f"provider={getattr(_embedding_client_instance, 'provider', 'unknown')}"
        )
        if _embedding_client_instance is not None:
            try:
                await _embedding_client_instance.close()
            except RuntimeError:
                # Event loop may be closed, ignore
                logger.debug("[EMBED-SHUTDOWN] RuntimeError on close (event loop likely closed)")
            _embedding_client_instance = None
            logger.debug("[EMBED-SHUTDOWN] Embedding client instance cleared")