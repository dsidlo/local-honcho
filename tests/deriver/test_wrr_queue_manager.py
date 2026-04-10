"""Integration tests for QueueManager WRR integration.

Tests that QueueManager correctly routes to WRR or FIFO based on configuration.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.deriver.queue_manager import QueueManager


class TestQueueManagerWRRRouting:
    """Test WRR/FIFO routing in QueueManager."""

    def test_methods_exist(self):
        """Test that all required methods exist on QueueManager."""
        # Create a minimal QueueManager
        with patch('src.deriver.queue_manager.settings') as mock_settings:
            mock_settings.DERIVER.WORKERS = 1
            mock_settings.DERIVER.WRR.ENABLED = False
            
            manager = QueueManager()
            
            # Check all required methods exist
            assert hasattr(manager, 'get_and_claim_work_units')
            assert hasattr(manager, '_get_and_claim_work_units_weighted')
            assert hasattr(manager, '_get_and_claim_work_units_fifo')
            assert hasattr(manager, '_query_task_type')
            assert hasattr(manager, '_record_wrr_metrics')

    @pytest.mark.asyncio
    async def test_routing_to_fifo_when_wrr_disabled(self):
        """Test that FIFO is used when WRR is disabled."""
        with patch('src.deriver.queue_manager.settings') as mock_settings:
            mock_settings.DERIVER.WORKERS = 1
            mock_settings.DERIVER.WRR.ENABLED = False
            
            manager = QueueManager()
            
            # Mock the FIFO method
            manager._get_and_claim_work_units_fifo = AsyncMock(return_value={"key1": "aqs1"})
            manager._get_and_claim_work_units_weighted = AsyncMock(return_value={"key2": "aqs2"})
            
            result = await manager.get_and_claim_work_units()
            
            # Should call FIFO, not weighted
            manager._get_and_claim_work_units_fifo.assert_called_once()
            manager._get_and_claim_work_units_weighted.assert_not_called()
            assert result == {"key1": "aqs1"}

    @pytest.mark.asyncio
    async def test_routing_to_weighted_when_wrr_enabled(self):
        """Test that weighted is used when WRR is enabled."""
        with patch('src.deriver.queue_manager.settings') as mock_settings:
            mock_settings.DERIVER.WORKERS = 1
            mock_settings.DERIVER.WRR.ENABLED = True
            
            manager = QueueManager()
            
            # Mock both methods
            manager._get_and_claim_work_units_fifo = AsyncMock(return_value={"key1": "aqs1"})
            manager._get_and_claim_work_units_weighted = AsyncMock(return_value={"key2": "aqs2"})
            
            result = await manager.get_and_claim_work_units()
            
            # Should call weighted, not FIFO
            manager._get_and_claim_work_units_weighted.assert_called_once()
            manager._get_and_claim_work_units_fifo.assert_not_called()
            assert result == {"key2": "aqs2"}

    @pytest.mark.asyncio
    async def test_backward_compatibility_default(self):
        """Test that default behavior (WRR disabled) uses FIFO."""
        # Import settings to check actual default
        from src.config import DeriverWRRSettings
        
        wrr_default = DeriverWRRSettings()
        assert wrr_default.ENABLED == False, "WRR should be disabled by default"


class TestQueueManagerWRRMethods:
    """Test individual WRR methods."""

    def test_record_wrr_metrics(self):
        """Test that _record_wrr_metrics handles allocation correctly."""
        with patch('src.deriver.queue_manager.settings') as mock_settings:
            mock_settings.DERIVER.WORKERS = 1
            mock_settings.DERIVER.WRR.ENABLED = False
            
            manager = QueueManager()
            
            # Should not raise
            manager._record_wrr_metrics(
                allocation={"representation": 5, "webhook": 3, "idle_fill": 2},
                total_requested=10,
            )
