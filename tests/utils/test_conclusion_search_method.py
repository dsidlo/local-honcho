"""Grok's dialectic requests RRF. The default conclusion search stays vector."""

from typing import Any

import pytest

from src.utils.agent_tools import search_memory


class _Session:
    def expunge(self, doc: object) -> None:
        return None


class _SessionCtx:
    async def __aenter__(self) -> _Session:
        return _Session()

    async def __aexit__(self, *_exc: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_default_search_memory_is_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    async def vector(*_args: Any, **_kwargs: Any) -> list[object]:
        seen.append("vector")
        return []

    async def hybrid(*_args: Any, **_kwargs: Any) -> list[object]:
        seen.append("rrf")
        return []

    monkeypatch.setattr("src.utils.agent_tools.crud.query_documents", vector)
    monkeypatch.setattr("src.utils.agent_tools.crud.query_documents_hybrid", hybrid)

    result = await search_memory(
        workspace_name="ws",
        observer="agent-grok",
        observed="agent-grok",
        query="llm_model_func",
        limit=3,
        embedding=[0.0],
    )

    assert seen == ["vector"]
    assert result.is_empty()


@pytest.mark.asyncio
async def test_rrf_search_memory_uses_hybrid(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def vector(*_args: Any, **_kwargs: Any) -> list[object]:
        raise AssertionError("vector search should not run")

    async def hybrid(*_args: Any, **kwargs: Any) -> list[object]:
        seen["method"] = kwargs["method"]
        seen["top_k"] = kwargs["top_k"]
        seen["observer"] = kwargs["observer"]
        return []

    monkeypatch.setattr("src.utils.agent_tools.crud.query_documents", vector)
    monkeypatch.setattr("src.utils.agent_tools.crud.query_documents_hybrid", hybrid)
    monkeypatch.setattr(
        "src.utils.agent_tools.tracked_db", lambda *_a, **_k: _SessionCtx()
    )

    result = await search_memory(
        workspace_name="ws",
        observer="agent-grok",
        observed="agent-grok",
        query="llm_model_func",
        limit=3,
        embedding=[0.0],
        method="rrf",
    )

    assert seen == {"method": "rrf", "top_k": 3, "observer": "agent-grok"}
    assert result.is_empty()
