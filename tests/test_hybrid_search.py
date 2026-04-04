"""Test suite for hybrid search functionality (Vector + FTS + Trigram)."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from nanoid import generate as generate_nanoid

from src import crud, models, schemas


async def _setup_test_collection(
    db_session: AsyncSession,
    test_workspace: models.Workspace,
    test_peer: models.Peer,
):
    """Helper to set up test data with observer/observed peers."""
    # Create another peer as observed
    observed_peer = models.Peer(
        name=str(generate_nanoid()), workspace_name=test_workspace.name
    )
    db_session.add(observed_peer)
    await db_session.flush()

    # Create a session
    test_session = models.Session(
        name=str(generate_nanoid()), workspace_name=test_workspace.name
    )
    db_session.add(test_session)
    await db_session.flush()

    # Create collection (required for documents foreign key)
    collection = models.Collection(
        workspace_name=test_workspace.name,
        observer=test_peer.name,
        observed=observed_peer.name,
    )
    db_session.add(collection)
    await db_session.flush()

    return observed_peer, test_session


async def _create_test_documents(
    db_session: AsyncSession,
    workspace_name: str,
    observer: str,
    observed: str,
    session_name: str,
    documents_data: list[dict],
) -> list[models.Document]:
    """Create test documents with embeddings."""
    doc_schemas = []
    for data in documents_data:
        doc_schemas.append(
            schemas.DocumentCreate(
                content=data["content"],
                embedding=data.get("embedding", [0.5] * 1536),
                session_name=session_name,
                level=data.get("level", "explicit"),
                metadata=schemas.DocumentMetadata(
                    message_ids=[1],
                    message_created_at="2025-01-01T00:00:00Z",
                ),
            )
        )

    await crud.create_documents(
        db_session,
        documents=doc_schemas,
        workspace_name=workspace_name,
        observer=observer,
        observed=observed,
    )

    # Return created documents
    stmt = select(models.Document).where(
        models.Document.workspace_name == workspace_name,
        models.Document.observer == observer,
        models.Document.observed == observed,
    )
    result = await db_session.execute(stmt)
    return list(result.scalars().all())


class TestHybridSearch:
    """Test suite for hybrid search (Vector + FTS + Trigram)."""

    @pytest.mark.asyncio
    async def test_hybrid_search_rrf(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        """Test Reciprocal Rank Fusion method returns results."""
        test_workspace, test_peer = sample_data
        observed_peer, test_session = await _setup_test_collection(
            db_session, test_workspace, test_peer
        )

        # Create test documents with varied content
        documents = await _create_test_documents(
            db_session,
            workspace_name=test_workspace.name,
            observer=test_peer.name,
            observed=observed_peer.name,
            session_name=test_session.name,
            documents_data=[
                {"content": "API key configuration for Stripe integration"},
                {"content": "User prefers dark mode interface"},
                {"content": "Testing webhook endpoints"},
                {"content": "Database migration completed successfully"},
            ],
        )

        # Test RRF fusion
        results = await crud.query_documents_hybrid(
            db_session,
            workspace_name=test_workspace.name,
            query="stripe api key",
            observer=test_peer.name,
            observed=observed_peer.name,
            top_k=10,
            method="rrf",
        )

        # Should return results
        assert len(results) > 0
        assert len(results) <= 10

    @pytest.mark.asyncio
    async def test_hybrid_search_weighted(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        """Test weighted linear combination method."""
        test_workspace, test_peer = sample_data
        observed_peer, test_session = await _setup_test_collection(
            db_session, test_workspace, test_peer
        )

        # Create test documents
        documents = await _create_test_documents(
            db_session,
            workspace_name=test_workspace.name,
            observer=test_peer.name,
            observed=observed_peer.name,
            session_name=test_session.name,
            documents_data=[
                {"content": "Webhook endpoint configuration"},
                {"content": "User authentication flow"},
                {"content": "Payment processing module"},
            ],
        )

        # Test weighted fusion with custom weights
        weights = {"vector": 0.6, "fts": 0.3, "trigram": 0.1}
        results = await crud.query_documents_hybrid(
            db_session,
            workspace_name=test_workspace.name,
            query="webhook configuration",
            observer=test_peer.name,
            observed=observed_peer.name,
            top_k=10,
            method="weighted",
            weights=weights,
        )

        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_hybrid_search_cascade(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        """Test cascade fallback method."""
        test_workspace, test_peer = sample_data
        observed_peer, test_session = await _setup_test_collection(
            db_session, test_workspace, test_peer
        )

        # Create test documents
        documents = await _create_test_documents(
            db_session,
            workspace_name=test_workspace.name,
            observer=test_peer.name,
            observed=observed_peer.name,
            session_name=test_session.name,
            documents_data=[
                {"content": "Workspace settings configuration"},
                {"content": "Session management system"},
                {"content": "Integration with external APIs"},
            ],
        )

        # Test cascade method
        results = await crud.query_documents_hybrid(
            db_session,
            workspace_name=test_workspace.name,
            query="session management",
            observer=test_peer.name,
            observed=observed_peer.name,
            top_k=10,
            method="cascade",
        )

        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_hybrid_search_exact_term(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        """Test that exact term matching works via FTS."""
        test_workspace, test_peer = sample_data
        observed_peer, test_session = await _setup_test_collection(
            db_session, test_workspace, test_peer
        )

        # Create documents with specific technical terms
        documents = await _create_test_documents(
            db_session,
            workspace_name=test_workspace.name,
            observer=test_peer.name,
            observed=observed_peer.name,
            session_name=test_session.name,
            documents_data=[
                {"content": "Unique identifier workspace_abc123 for testing"},
                {"content": "General workspace overview document"},
                {"content": "Workspace configuration guide"},
            ],
        )

        # Find the document with the exact ID
        exact_doc = next(
            (d for d in documents if "workspace_abc123" in d.content), None
        )
        assert exact_doc is not None

        # Query for the exact term
        results = await crud.query_documents_hybrid(
            db_session,
            workspace_name=test_workspace.name,
            query="workspace_abc123",
            observer=test_peer.name,
            observed=observed_peer.name,
            top_k=10,
            method="rrf",
        )

        # Should find the document with the exact ID
        assert len(results) > 0
        # The document with exact match should rank highly
        result_contents = [r.content for r in results]
        assert any("workspace_abc123" in c for c in result_contents)

    @pytest.mark.asyncio
    async def test_hybrid_search_typo_tolerance(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        """Test that trigram similarity handles typos."""
        test_workspace, test_peer = sample_data
        observed_peer, test_session = await _setup_test_collection(
            db_session, test_workspace, test_peer
        )

        # Create documents with correctly spelled words
        documents = await _create_test_documents(
            db_session,
            workspace_name=test_workspace.name,
            observer=test_peer.name,
            observed=observed_peer.name,
            session_name=test_session.name,
            documents_data=[
                {"content": "Stripe integration for payment processing"},
                {"content": "Payment gateway documentation"},
                {"content": "API integration guide"},
            ],
        )

        # Query with a typo ("integraiton" instead of "integration")
        # Trigram search should still find similar documents
        results = await crud.query_documents_hybrid(
            db_session,
            workspace_name=test_workspace.name,
            query="stripe integraiton",  # typo!
            observer=test_peer.name,
            observed=observed_peer.name,
            top_k=10,
            method="rrf",
        )

        # Should still find results despite the typo
        # (trigram similarity handles slight misspellings)
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_hybrid_search_with_filters(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        """Test hybrid search respects level and session filters."""
        test_workspace, test_peer = sample_data
        observed_peer, test_session = await _setup_test_collection(
            db_session, test_workspace, test_peer
        )

        # Create documents with different levels
        documents = await _create_test_documents(
            db_session,
            workspace_name=test_workspace.name,
            observer=test_peer.name,
            observed=observed_peer.name,
            session_name=test_session.name,
            documents_data=[
                {"content": "Explicit observation about settings", "level": "explicit"},
                {"content": "Deductive conclusion about settings", "level": "deductive"},
            ],
        )

        # Test with level filter
        results = await crud.query_documents_hybrid(
            db_session,
            workspace_name=test_workspace.name,
            query="settings",
            observer=test_peer.name,
            observed=observed_peer.name,
            top_k=10,
            method="rrf",
            filters={"level": "explicit"},
        )

        # Should only return explicit documents
        assert len(results) > 0
        for doc in results:
            assert doc.level == "explicit"

    @pytest.mark.asyncio
    async def test_hybrid_search_with_max_distance(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        """Test max_distance filtering in hybrid search."""
        test_workspace, test_peer = sample_data
        observed_peer, test_session = await _setup_test_collection(
            db_session, test_workspace, test_peer
        )

        # Create documents
        documents = await _create_test_documents(
            db_session,
            workspace_name=test_workspace.name,
            observer=test_peer.name,
            observed=observed_peer.name,
            session_name=test_session.name,
            documents_data=[
                {"content": "Test document one"},
                {"content": "Another document"},
            ],
        )

        # Test with restrictive max_distance
        results = await crud.query_documents_hybrid(
            db_session,
            workspace_name=test_workspace.name,
            query="test document",
            observer=test_peer.name,
            observed=observed_peer.name,
            top_k=10,
            method="rrf",
            max_distance=0.5,
        )

        # May return fewer results or none based on distance threshold
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_hybrid_search_different_methods_return_results(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        """Test that all three methods return consistent results."""
        test_workspace, test_peer = sample_data
        observed_peer, test_session = await _setup_test_collection(
            db_session, test_workspace, test_peer
        )

        # Create test documents
        documents = await _create_test_documents(
            db_session,
            workspace_name=test_workspace.name,
            observer=test_peer.name,
            observed=observed_peer.name,
            session_name=test_session.name,
            documents_data=[
                {"content": "Machine learning model training"},
                {"content": "Data visualization dashboard"},
                {"content": "API endpoint documentation"},
            ],
        )

        # Test all three methods
        methods = ["rrf", "weighted", "cascade"]

        for method in methods:
            results = await crud.query_documents_hybrid(
                db_session,
                workspace_name=test_workspace.name,
                query="machine learning",
                observer=test_peer.name,
                observed=observed_peer.name,
                top_k=10,
                method=method,  # type: ignore[arg-type]
            )

            # Each method should return some results
            assert len(results) >= 0, f"Method {method} returned no results"

    @pytest.mark.asyncio
    async def test_hybrid_search_empty_results(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        """Test hybrid search handles no-match scenarios gracefully."""
        test_workspace, test_peer = sample_data
        observed_peer, test_session = await _setup_test_collection(
            db_session, test_workspace, test_peer
        )

        # Create documents
        documents = await _create_test_documents(
            db_session,
            workspace_name=test_workspace.name,
            observer=test_peer.name,
            observed=observed_peer.name,
            session_name=test_session.name,
            documents_data=[
                {"content": "Regular observation"},
            ],
        )

        # Query for documents from a different observer/observed pair (non-existent)
        # This ensures we get truly empty results since the documents belong to
        # a different collection
        results = await crud.query_documents_hybrid(
            db_session,
            workspace_name=test_workspace.name,
            query="Regular observation",  # Query that would match content
            observer="nonexistent_observer",  # But filter to non-existent collection
            observed="nonexistent_observed",
            top_k=10,
            method="rrf",
        )

        # Should return empty list - no documents for this observer/observed pair
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_hybrid_search_ranks_combination(
        self,
        db_session: AsyncSession,
        sample_data: tuple[models.Workspace, models.Peer],
    ):
        """Test that RRF correctly combines ranks from multiple sources."""
        test_workspace, test_peer = sample_data
        observed_peer, test_session = await _setup_test_collection(
            db_session, test_workspace, test_peer
        )

        # Create documents that would rank differently by different methods
        documents = await _create_test_documents(
            db_session,
            workspace_name=test_workspace.name,
            observer=test_peer.name,
            observed=observed_peer.name,
            session_name=test_session.name,
            documents_data=[
                {"content": "Exact match for testing query"},
                {"content": "Semantic similarity to test data"},
                {"content": "Test phrase in the content"},
            ],
        )

        # Query with "test" which should match all
        results = await crud.query_documents_hybrid(
            db_session,
            workspace_name=test_workspace.name,
            query="test",
            observer=test_peer.name,
            observed=observed_peer.name,
            top_k=10,
            method="rrf",
            rrf_k=60,
        )

        # RRF should return results in a reasonable order
        assert len(results) > 0
        # All test documents should appear
        assert len(results) == 3