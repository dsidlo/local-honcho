"""Regression tests for query_documents_hybrid filters.

Guards against the psycopg "cannot adapt type 'dict'" failure that occurred
when operator-style filter values ({"level": {"in": [...]}}) were bound raw
instead of being compiled through apply_filter.
"""

from typing import Any

import pytest
import sqlalchemy as sa

from src import models
from src.utils.agent_tools import search_memory
from src.utils.filter import apply_filter


class TestHybridFilterValues:
    def test_in_operator_compiles_to_in_clause(self) -> None:
        stmt = apply_filter(
            sa.select(models.Document.id), models.Document, {"level": {"in": ["explicit"]}}
        )
        compiled = stmt.compile()

        # The bound parameter must be a list (adapted to an IN clause), not a dict
        level_params = [(k, v) for k, v in compiled.params.items() if "level" in k]
        assert level_params, "no level bind parameter produced"
        assert all(
            not isinstance(v, dict) for _, v in level_params
        ), f"dict bound raw: {level_params}"
        in_params = [(k, v) for k, v in level_params if isinstance(v, list)]
        assert in_params, f"expected list param: {level_params}"

    def test_plain_equality_still_works_through_apply_filter(
        self,
    ) -> None:
        stmt = apply_filter(
            sa.select(models.Document.id), models.Document, {"level": "explicit"}
        )
        compiled = stmt.compile()
        level_params = [(k, v) for k, v in compiled.params.items() if "level" in k]
        assert level_params
        assert level_params[0][1] == "explicit"

    @pytest.mark.asyncio
    async def test_search_memory_accepts_operator_filters(
        self, db_session: Any
    ) -> None:
        """End-to-end: search_memory with {"in": [...]} must not blow up."""
        reps = await search_memory(
            db=db_session,
            workspace_name="filter-op-workspace",
            observer="filter-op-observer",
            observed="filter-op-observed",
            query="anything",
            limit=3,
            levels=["explicit"],
            embedding=[0.0] * 1536,
        )
        assert reps.explicit == []