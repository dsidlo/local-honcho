"""Unit tests for src/reranker_client.py.

Covers the _RerankerClient class, the singleton factory, and the module-level
convenience function. All HTTP interactions are mocked; no real Ollama or
embedding provider is called.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from src.reranker_client import (
    RerankedDocument,
    _RerankerClient,
    get_reranker_client,
    rerank_documents,
)


def _doc(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content)


def _reranker_settings(
    enabled: bool = True,
    provider: str = "ollama",
    top_k: int = 2,
    batch_size: int = 2,
) -> MagicMock:
    base = MagicMock()
    base.ENABLED = enabled
    base.PROVIDER = provider
    base.MODEL = "test-model"
    base.OLLAMA_BASE_URL = "http://ollama.test:11434"
    base.TIMEOUT_SECONDS = 30
    base.TOP_K = top_k
    base.BATCH_SIZE = batch_size
    return base


@pytest_asyncio.fixture
def client():
    """A client instantiated with a mocked RERANKER settings namespace."""
    with patch("src.reranker_client.settings") as mock_settings:
        mock_settings.RERANKER = _reranker_settings()
        yield _RerankerClient()


class TestInit:
    def test_enabled_logs_info(self):
        with patch("src.reranker_client.settings") as mock_settings:
            mock_settings.RERANKER = _reranker_settings(enabled=True)
            with patch("src.reranker_client.logger.info") as mock_info:
                _RerankerClient()
                mock_info.assert_called_once()

    def test_disabled_logs_debug(self):
        with patch("src.reranker_client.settings") as mock_settings:
            mock_settings.RERANKER = _reranker_settings(enabled=False)
            with patch("src.reranker_client.logger.debug") as mock_debug:
                _RerankerClient()
                mock_debug.assert_called_once()


class TestRerankNoop:
    async def test_disabled_returns_documents(self):
        with patch("src.reranker_client.settings") as mock_settings:
            mock_settings.RERANKER = _reranker_settings(enabled=False)
            c = _RerankerClient()
            docs = [_doc("a"), _doc("b")]
            assert await c.rerank("q", docs) == docs

    async def test_empty_documents(self, client):
        assert await client.rerank("q", []) == []

    async def test_len_le_top_k_noop(self, client):
        docs = [_doc("a"), _doc("b")]
        assert await client.rerank("q", docs) == docs

    async def test_unknown_provider_returns_top_k(self):
        with patch("src.reranker_client.settings") as mock_settings:
            mock_settings.RERANKER = _reranker_settings(provider="unknown")
            c = _RerankerClient()
            docs = [_doc(str(i)) for i in range(5)]
            result = await c.rerank("q", docs)
            assert result == docs[: c.top_k]

    async def test_exception_falls_back_to_top_k(self, client):
        with patch.object(client, "_rerank_ollama", side_effect=RuntimeError("boom")):
            docs = [_doc(str(i)) for i in range(5)]
            result = await client.rerank("q", docs)
            assert result == docs[: client.top_k]


class TestRerankOllama:
    @staticmethod
    def _configure_httpx(mock_client):
        """Make `async with httpx.AsyncClient()` return mock_client itself."""
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False
        return mock_client

    async def test_results_format_sorts_and_truncates(self, client):
        docs = [_doc(str(i)) for i in range(5)]
        results = [{"index": 3, "score": 0.9}, {"index": 1, "score": 0.5}]

        async def post(url, **kwargs):
            resp = MagicMock()
            resp.json.return_value = {"results": results}
            return resp

        mock_client = self._configure_httpx(AsyncMock())
        mock_client.post.side_effect = post
        with patch("httpx.AsyncClient", return_value=mock_client):
            out = await client._rerank_ollama("q", docs, 2)
        assert [d.content for d in out] == [docs[3].content, docs[1].content]

    async def test_scores_format(self, client):
        docs = [_doc(str(i)) for i in range(3)]
        scores = [0.2, 0.9, 0.5]

        async def post(url, **kwargs):
            resp = MagicMock()
            resp.json.return_value = {"scores": scores}
            return resp

        mock_client = self._configure_httpx(AsyncMock())
        mock_client.post.side_effect = post
        with patch("httpx.AsyncClient", return_value=mock_client):
            out = await client._rerank_ollama("q", docs, 3)
        assert [d.content for d in out] == [docs[1].content, docs[2].content, docs[0].content]

    async def test_unexpected_format_returns_top_k(self, client):
        docs = [_doc(str(i)) for i in range(5)]

        async def post(url, **kwargs):
            resp = MagicMock()
            resp.json.return_value = {"something": "else"}
            return resp

        mock_client = self._configure_httpx(AsyncMock())
        mock_client.post.side_effect = post
        with patch("httpx.AsyncClient", return_value=mock_client):
            out = await client._rerank_ollama("q", docs, 2)
        assert out == docs[:2]

    async def test_http_status_error_falls_back_to_embeddings(self, client):
        docs = [_doc("doc content") for _ in range(3)]

        async def post(url, **kwargs):
            if "rerank" in url:
                raise httpx.HTTPStatusError("404", request=httpx.Request("POST", url), response=httpx.Response(404))
            resp = MagicMock()
            resp.json.return_value = {"embedding": [1.0, 0.0]}
            return resp

        mock_client = self._configure_httpx(AsyncMock())
        mock_client.post.side_effect = post
        with patch("httpx.AsyncClient", return_value=mock_client), patch.object(
            client, "_rerank_via_embeddings", new=AsyncMock(return_value=docs[:2])
        ) as mock_fallback:
            out = await client._rerank_ollama("q", docs, 2)
        mock_fallback.assert_awaited_once()
        assert out == docs[:2]


class TestRerankViaEmbeddings:
    async def test_cosine_similarity_top_k(self):
        with patch("src.reranker_client.settings") as mock_settings:
            mock_settings.RERANKER = _reranker_settings()
            c = _RerankerClient()

        docs = [_doc("doc-0"), _doc("doc-1"), _doc("doc-2"), _doc("doc-3")]

        async def post(url, **kwargs):
            resp = MagicMock()
            prompt = kwargs.get("prompt")
            if prompt == "q":
                resp.json.return_value = {"embedding": [1.0, 0.0]}
            elif prompt == "doc-0":
                # aligned with query -> similarity 1.0, highest
                resp.json.return_value = {"embedding": [1.0, 0.0]}
            else:
                # orthogonal -> similarity 0.0
                resp.json.return_value = {"embedding": [0.0, 1.0]}
            return resp

        mock_client = AsyncMock()
        mock_client.post.side_effect = post
        out = await c._rerank_via_embeddings(mock_client, "q", docs, 2)
        assert len(out) == 2
        assert out[0] is docs[0]


class TestSingletonAndConvenience:
    def test_get_reranker_client_singleton(self):
        with patch("src.reranker_client.settings") as mock_settings:
            mock_settings.RERANKER = _reranker_settings()
            first = get_reranker_client()
            second = get_reranker_client()
            assert first is second

    def test_get_reranker_client_caches(self):
        with patch("src.reranker_client.settings") as mock_settings:
            mock_settings.RERANKER = _reranker_settings()
            with patch.object(_RerankerClient, "__init__", return_value=None):
                a = get_reranker_client()
                b = get_reranker_client()
                assert a is b

    async def test_rerank_documents(self):
        with patch("src.reranker_client.settings") as mock_settings:
            mock_settings.RERANKER = _reranker_settings()
            with patch(
                "src.reranker_client.get_reranker_client"
            ) as mock_get:
                stub = MagicMock()
                stub.rerank = AsyncMock(return_value=[_doc("a")])
                mock_get.return_value = stub
                out = await rerank_documents("q", [_doc("a"), _doc("b")])
                assert out[0].content == "a"

    def test_rerankeddocument_namedtuple(self):
        doc = _doc("x")
        rd = RerankedDocument(document=doc, score=0.7)
        assert rd.document is doc
        assert rd.score == 0.7
