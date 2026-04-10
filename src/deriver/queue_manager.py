import asyncio
import signal
from asyncio import Task
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from logging import getLogger
from typing import Any, NamedTuple, cast

import sentry_sdk
from dotenv import load_dotenv
from nanoid import generate as generate_nanoid
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from src import models
from src.cache.client import close_cache, init_cache
from src.config import settings
from src.dependencies import tracked_db
from src.deriver.consumer import (
    process_item,
    process_representation_batch,
)
from src.deriver.error_tracker import WorkUnitErrorTracker
from src.deriver.wrr_metrics import (
    record_idle_fill,
    record_quota_calculation,
    record_task_type_query,
    record_wrr_poll_summary,
)
from src.deriver.wrr_queries import (
    calculate_wrr_quotas,
    query_idle_fill_work_units,
    query_task_type_work_units,
)
from src.dreamer.dream_scheduler import (
    DreamScheduler,
    get_dream_scheduler,
    set_dream_scheduler,
)
from src.models import QueueItem
from src.reconciler import (
    ReconcilerScheduler,
    get_reconciler_scheduler,
    set_reconciler_scheduler,
)
from src.schemas import ResolvedConfiguration
from src.telemetry import prometheus_metrics
from src.telemetry.sentry import initialize_sentry
from src.utils.work_unit import parse_work_unit_key
from src.webhooks.events import (
    QueueEmptyEvent,
    publish_webhook_event,
)

logger = getLogger(__name__)

load_dotenv(override=True)


class WorkerOwnership(NamedTuple):
    """Represents the instance of a work unit that a worker is processing."""

    work_unit_key: str
    aqs_id: str  # The ID of the ActiveQueueSession that the worker is processing


class QueueManager:
    def __init__(self):
        self.shutdown_event: asyncio.Event = asyncio.Event()
        self.active_tasks: set[asyncio.Task[None]] = set()
        self.worker_ownership: dict[str, WorkerOwnership] = {}
        self.queue_empty_flag: asyncio.Event = asyncio.Event()

        # Initialize from settings
        self.workers: int = settings.DERIVER.WORKERS
        self.semaphore: asyncio.Semaphore = asyncio.Semaphore(self.workers)

        # Get or create the singleton dream scheduler
        existing_scheduler = get_dream_scheduler()
        if existing_scheduler is None:
            self.dream_scheduler: DreamScheduler = DreamScheduler()
            set_dream_scheduler(self.dream_scheduler)
        else:
            self.dream_scheduler = existing_scheduler

        # Get or create the singleton reconciler scheduler
        existing_reconciler = get_reconciler_scheduler()
        if existing_reconciler is None:
            self.reconciler_scheduler: ReconcilerScheduler = ReconcilerScheduler()
            set_reconciler_scheduler(self.reconciler_scheduler)
        else:
            self.reconciler_scheduler = existing_reconciler

        # Initialize Sentry if enabled, using settings
        if settings.SENTRY.ENABLED:
            initialize_sentry(integrations=[AsyncioIntegration()])

        # In-memory error tracker for retry/backoff logic
        self.error_tracker = WorkUnitErrorTracker()

    def add_task(self, task: asyncio.Task[None]) -> None:
        """Track a new task"""
        self.active_tasks.add(task)
        task.add_done_callback(self.active_tasks.discard)

    def track_worker_work_unit(
        self, worker_id: str, work_unit_key: str, aqs_id: str
    ) -> None:
        """Track a work unit owned by a specific worker"""
        self.worker_ownership[worker_id] = WorkerOwnership(work_unit_key, aqs_id)

    def untrack_worker_work_unit(self, worker_id: str, work_unit_key: str) -> None:
        """Remove a work unit from worker tracking"""
        ownership = self.worker_ownership.get(worker_id)
        if ownership and ownership.work_unit_key == work_unit_key:
            del self.worker_ownership[worker_id]

    def create_worker_id(self) -> str:
        """Generate a unique worker ID for this processing task"""
        return generate_nanoid()

    def get_total_owned_work_units(self) -> int:
        """Get the total number of work units owned by all workers"""
        return len(self.worker_ownership)

    async def initialize(self) -> None:
        """Setup signal handlers, initialize client, and start the main polling loop"""
        logger.info(f"Initializing QueueManager with {self.workers} workers")

        # Set up signal handlers
        loop = asyncio.get_running_loop()
        signals = (signal.SIGTERM, signal.SIGINT)
        for sig in signals:
            loop.add_signal_handler(
                sig, lambda s=sig: asyncio.create_task(self.shutdown(s))
            )
        logger.info("Signal handlers registered")

        # Start the reconciler scheduler
        try:
            await self.reconciler_scheduler.start()
        except Exception:
            logger.exception("Failed to start reconciler scheduler")

        # Run the polling loop directly in this task
        logger.info("Starting polling loop directly")
        try:
            await self.polling_loop()
        finally:
            await self.cleanup()

    async def shutdown(self, sig: signal.Signals) -> None:
        """Handle graceful shutdown"""
        logger.info(f"Received exit signal {sig.name}...")
        self.shutdown_event.set()

        # Cancel all pending dreams
        await self.dream_scheduler.shutdown()

        # Stop the reconciler scheduler
        await self.reconciler_scheduler.shutdown()

        if self.active_tasks:
            logger.info(
                f"Waiting for {len(self.active_tasks)} active tasks to complete..."
            )
            await asyncio.gather(*self.active_tasks, return_exceptions=True)

    async def cleanup(self) -> None:
        """Clean up owned work units"""
        total_work_units = self.get_total_owned_work_units()
        if total_work_units > 0:
            logger.info(f"Cleaning up {total_work_units} owned work units...")
            try:
                # Use the tracked_db dependency for transaction safety
                async with tracked_db("queue_cleanup") as db:
                    aqs_ids = [
                        ownership.aqs_id for ownership in self.worker_ownership.values()
                    ]
                    if aqs_ids:
                        await db.execute(
                            delete(models.ActiveQueueSession).where(
                                models.ActiveQueueSession.id.in_(aqs_ids)
                            )
                        )
                    await db.commit()
            except Exception as e:
                logger.error(f"Error during cleanup: {str(e)}")
                if settings.SENTRY.ENABLED:
                    sentry_sdk.capture_exception(e)
            finally:
                self.worker_ownership.clear()

    ##########################
    # Polling and Scheduling #
    ##########################

    async def cleanup_stale_work_units(self) -> None:
        """Clean up stale work units"""
        async with tracked_db("cleanup_stale_work_units") as db:
            cutoff = datetime.now(timezone.utc) - timedelta(
                minutes=settings.DERIVER.STALE_SESSION_TIMEOUT_MINUTES
            )

            stale_ids = (
                (
                    await db.execute(
                        select(models.ActiveQueueSession.id)
                        .where(models.ActiveQueueSession.last_updated < cutoff)
                        .order_by(models.ActiveQueueSession.last_updated)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )

            # Delete only the records we successfully got locks for
            if stale_ids:
                await db.execute(
                    delete(models.ActiveQueueSession).where(
                        models.ActiveQueueSession.id.in_(stale_ids)
                    )
                )
            await db.commit()

    async def get_and_claim_work_units(self) -> dict[str, str]:
        """
        Get available work units that aren't being processed.
        
        If WRR is enabled, uses Weighted Round-Robin scheduling.
        Otherwise, uses the original FIFO implementation.
        
        Returns a dict mapping work_unit_key to aqs_id.
        """
        if settings.DERIVER.WRR.ENABLED:
            return await self._get_and_claim_work_units_weighted()
        else:
            return await self._get_and_claim_work_units_fifo()

    async def _get_and_claim_work_units_weighted(self) -> dict[str, str]:
        """
        Get and claim work units using Weighted Round-Robin scheduling.
        
        RACE SAFETY: This method uses an "optimistic querying" pattern with
        atomic claim validation. It does not use pessimistic locking. The
        claim_work_units() method uses PostgreSQL's INSERT ... ON CONFLICT 
        for atomic claim semantics. See design doc for race condition details.
        
        Algorithm:
        1. Calculate quotas based on configured weights
        2. Query each task type up to its quota
        3. Handle idle_fill quota explicitly
        4. Claim work units atomically via INSERT ON CONFLICT
        5. Record metrics if enabled
        """
        wrr_config = settings.DERIVER.WRR
        
        # Calculate available capacity
        limit: int = max(0, self.workers - self.get_total_owned_work_units())
        if limit == 0:
            return {}
        
        async with tracked_db("get_available_work_units_weighted") as db:
            # Calculate quotas per task type
            quotas = calculate_wrr_quotas(
                available_workers=limit,
                weights=wrr_config.WEIGHTS,
                min_slots=wrr_config.MIN_SLOTS,
                max_slots=wrr_config.MAX_SLOTS,
            )
            
            if wrr_config.DEBUG_LOGGING:
                logger.info(f"WRR Quotas: {quotas}")
            
            # Track allocations
            all_work_units: list[str] = []
            task_type_allocation: dict[str, int] = {}
            
            # Phase 1: Query each task type (excluding idle_fill)
            for task_type, quota in quotas.items():
                if task_type == "idle_fill" or quota <= 0:
                    continue
                
                work_units = await self._query_task_type(
                    db=db,
                    task_type=task_type,
                    limit=quota,
                )
                
                # Filter out work units that are in error backoff
                before_filter = len(work_units)
                work_units = [
                    wu for wu in work_units
                    if not self.error_tracker.is_backed_off(wu)
                ]
                filtered_count = before_filter - len(work_units)
                if filtered_count > 0:
                    logger.info(
                        f"WRR Filtered {filtered_count} backed-off {task_type} work units"
                    )
                
                all_work_units.extend(work_units)
                task_type_allocation[task_type] = len(work_units)
                
                # Record task type query metrics
                record_task_type_query(task_type, quota, len(work_units))
                
                if wrr_config.DEBUG_LOGGING:
                    logger.info(
                        f"WRR Queried {task_type}: "
                        f"requested={quota}, returned={len(work_units)}"
                    )
            
            # Phase 2: Handle idle_fill quota explicitly
            idle_fill_quota = quotas.get("idle_fill", 0)
            idle_fill_filled = 0
            if idle_fill_quota > 0:
                # Get already-claimed work units to exclude
                claimed_work_units = set(all_work_units)
                
                fill_units = await query_idle_fill_work_units(
                    db=db,
                    limit=idle_fill_quota,
                    exclude_work_units=claimed_work_units,
                    strategy=wrr_config.IDLE_FILL_STRATEGY,
                )
                # Filter out work units that are in error backoff
                fill_units = [
                    wu for wu in fill_units
                    if not self.error_tracker.is_backed_off(wu)
                ]
                all_work_units.extend(fill_units)
                idle_fill_filled = len(fill_units)
                task_type_allocation["idle_fill"] = idle_fill_filled
                
                # Record idle fill metrics
                record_idle_fill(idle_fill_filled, idle_fill_quota, wrr_config.IDLE_FILL_STRATEGY)
                
                if wrr_config.DEBUG_LOGGING:
                    logger.info(
                        f"WRR Idle fill: {len(fill_units)}/{idle_fill_quota} units"
                    )
            
            # Record summary metrics
            record_wrr_poll_summary(
                quotas=quotas,
                allocations=task_type_allocation,
                idle_fill_requested=idle_fill_quota,
                idle_fill_filled=idle_fill_filled,
                strategy=wrr_config.IDLE_FILL_STRATEGY,
            )
            
            # Claim work units
            if not all_work_units:
                await db.commit()
                return {}
            
            # NOTE: claim_work_units() performs atomic INSERT ... ON CONFLICT.
            # If another worker claimed a work unit between query and claim,
            # it will be excluded from results. This is expected behavior.
            claimed_mapping = await self.claim_work_units(db, all_work_units)
            await db.commit()
            
            return claimed_mapping

    def _record_wrr_metrics(
        self,
        allocation: dict[str, int],
        total_requested: int,
    ) -> None:
        """
        Record WRR allocation metrics.
        
        DEPRECATED: Use wrr_metrics module directly instead.
        Kept for backward compatibility.
        """
        quotas = {k: v for k, v in allocation.items()}
        record_quota_calculation(quotas, allocation)

    async def _query_task_type(
        self,
        db: AsyncSession,
        task_type: str,
        limit: int,
    ) -> list[str]:
        """
        Query available work units for a specific task type.
        
        This is a simple wrapper around the wrr_queries module functions
        that handles task type dispatching.
        """
        return await query_task_type_work_units(db, task_type, limit)

    async def _get_and_claim_work_units_fifo(self) -> dict[str, str]:
        """
        Get available work units that aren't being processed.
        For representation tasks, only returns work units with accumulated tokens
        >= REPRESENTATION_BATCH_MAX_TOKENS (forced batching), unless FLUSH_ENABLED is True.
        Returns a dict mapping work_unit_key to aqs_id.
        """
        limit: int = max(0, self.workers - self.get_total_owned_work_units())
        logger.info(f"DEBUG: Workers={self.workers}, Owned={self.get_total_owned_work_units()}, Limit={limit}")
        if limit == 0:
            return {}

        batch_max_tokens = settings.DERIVER.REPRESENTATION_BATCH_MAX_TOKENS

        async with tracked_db("get_available_work_units") as db:
            representation_prefix = "representation:"
            token_stats_subq = (
                select(
                    models.QueueItem.work_unit_key,
                    func.sum(models.Message.token_count).label("total_tokens"),
                )
                .join(
                    models.Message,
                    models.QueueItem.message_id == models.Message.id,
                )
                .where(~models.QueueItem.processed)
                .where(models.QueueItem.work_unit_key.startswith(representation_prefix))
                .group_by(models.QueueItem.work_unit_key)
                .subquery()
            )

            work_units_subq = (
                select(
                    models.QueueItem.work_unit_key,
                    func.min(models.Message.created_at).label("oldest_message_at"),
                )
                .join(models.Message, models.QueueItem.message_id == models.Message.id)
                .where(~models.QueueItem.processed)
                .group_by(models.QueueItem.work_unit_key)
                .subquery()
            )

            # Filter out work units where the referenced messages don't exist
            # or are in a different session/workspace than expected.
            # This can happen due to data inconsistency (orphaned queue items).
            valid_messages_subq = (
                select(models.QueueItem.work_unit_key)
                .join(models.Message, models.QueueItem.message_id == models.Message.id)
                .where(~models.QueueItem.processed)
                .group_by(models.QueueItem.work_unit_key)
                .having(func.count(models.Message.id) > 0)
                .subquery()
            )

            query = (
                select(work_units_subq.c.work_unit_key, work_units_subq.c.oldest_message_at)
                .limit(limit)
                .outerjoin(
                    token_stats_subq,
                    work_units_subq.c.work_unit_key == token_stats_subq.c.work_unit_key,
                )
                .where(
                    ~select(models.ActiveQueueSession.id)
                    .where(
                        models.ActiveQueueSession.work_unit_key
                        == work_units_subq.c.work_unit_key
                    )
                    .exists()
                )
                .where(
                    # Only validate messages for task types that require them.
                    # Webhook, reconciler, and dream tasks don't have message_id.
                    or_(
                        ~work_units_subq.c.work_unit_key.startswith(representation_prefix),
                        work_units_subq.c.work_unit_key.in_(
                            select(valid_messages_subq.c.work_unit_key)
                        ),
                    )
                )
                .order_by(work_units_subq.c.oldest_message_at)
            )

            # Apply batch threshold filter (skip if FLUSH_ENABLED is True)
            if not settings.DERIVER.FLUSH_ENABLED and batch_max_tokens > 0:
                query = query.where(
                    or_(
                        ~work_units_subq.c.work_unit_key.startswith(
                            representation_prefix
                        ),
                        func.coalesce(token_stats_subq.c.total_tokens, 0)
                        >= batch_max_tokens,
                    )
                )

            # DEBUG: Log the actual SQL being executed
            from sqlalchemy.dialects import postgresql
            sql_str = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            with open("/tmp/deriver_query.sql", "a") as f:
                f.write(f"\n--- {datetime.now()} ---\n")
                f.write(sql_str)
                f.write("\n")
            logger.info(f"DEBUG SQL length: {len(sql_str)} chars, saved to /tmp/deriver_query.sql")

            result = await db.execute(query)
            rows = result.all()
            available_units = [row[0] for row in rows]
            # Also log the timestamps - now row has 2 columns: work_unit_key, oldest_created_at
            if rows:
                logger.info(f"DEBUG: Query returned {len(rows)} rows with timestamps: {[(row[0], row[1]) for row in rows[:5]]}")
            logger.info(f"DEBUG: Query returned {len(available_units)} available work units: {available_units[:10]}")  # Log first 10
            if not available_units:
                await db.commit()
                return {}

            logger.info(f"DEBUG: About to claim {len(available_units)} work units")
            claimed_mapping = await self.claim_work_units(db, available_units)
            logger.info(f"DEBUG: Successfully claimed {len(claimed_mapping)} work units: {list(claimed_mapping.keys())}")
            
            # NEW: Filter out work units that would return 0 items from get_queue_item_batch.
            # This can happen when queue items reference messages that don't exist
            # in the expected session/workspace (data inconsistency).
            if claimed_mapping:
                filtered_mapping = {}
                orphaned_count = 0
                for work_unit_key, aqs_id in claimed_mapping.items():
                    work_unit = parse_work_unit_key(work_unit_key)
                    if work_unit.task_type == "representation":
                        # Check if there are ANY unprocessed queue items with messages
                        # (not just in expected session - session may have changed/migrated)
                        result = await db.execute(
                            select(func.count(models.Message.id))
                            .select_from(models.QueueItem)
                            .join(
                                models.Message,
                                models.QueueItem.message_id == models.Message.id,
                            )
                            .where(models.QueueItem.work_unit_key == work_unit_key)
                            .where(~models.QueueItem.processed)
                        )
                        msg_count = result.scalar()
                        if msg_count == 0:
                            orphaned_count += 1
                            logger.warning(
                                f"Marked orphan as errored: {work_unit_key} - "
                                f"no linked messages found"
                            )
                            # Mark all queue items for this orphan as errored
                            await db.execute(
                                update(models.QueueItem)
                                .where(models.QueueItem.work_unit_key == work_unit_key)
                                .where(~models.QueueItem.processed)
                                .values(
                                    processed=True,
                                    error="Orphaned work unit: no linked messages"
                                )
                            )
                            # Clean up the AQS entry for the empty work unit
                            await db.execute(
                                delete(models.ActiveQueueSession).where(
                                    models.ActiveQueueSession.id == aqs_id
                                )
                            )
                            continue
                    filtered_mapping[work_unit_key] = aqs_id
                claimed_mapping = filtered_mapping
                logger.info(f"DEBUG: Filtered out {orphaned_count} orphaned work units, returning {len(filtered_mapping)} valid work units")
            else:
                logger.info("DEBUG: No work units were claimed (claimed_mapping was empty)")
            
            await db.commit()

            return claimed_mapping

    async def claim_work_units(
        self, db: AsyncSession, work_unit_keys: Sequence[str]
    ) -> dict[str, str]:
        """
        Claim work units and return a mapping of work_unit_key to aqs_id.
        Returns only the work units that were successfully claimed.
        Filters out work units that no longer have unprocessed queue items.
        """
        # First, filter to only work units that still have unprocessed queue items.
        # For representation/summary tasks, also validate that messages exist.
        # Webhook, reconciler, and dream tasks don't require message validation.
        
        # Get work units that don't need message validation (webhook, reconciler, dream)
        no_validation_needed_query = (
            select(models.QueueItem.work_unit_key)
            .where(
                models.QueueItem.work_unit_key.in_(work_unit_keys),
                ~models.QueueItem.processed,
            )
            .where(
                ~models.QueueItem.work_unit_key.startswith("representation:")
            )
            .group_by(models.QueueItem.work_unit_key)
            .having(func.count(models.QueueItem.id) > 0)
        )
        
        # Get work units that need message validation (representation, summary)
        needs_validation_query = (
            select(models.QueueItem.work_unit_key)
            .join(models.Message, models.QueueItem.message_id == models.Message.id)
            .where(
                models.QueueItem.work_unit_key.in_(work_unit_keys),
                ~models.QueueItem.processed,
            )
            .where(
                models.QueueItem.work_unit_key.startswith("representation:")
            )
            .group_by(models.QueueItem.work_unit_key)
            .having(func.count(models.QueueItem.id) > 0)
            .having(func.count(models.Message.id) > 0)
        )
        
        # Combine both sets of valid work units
        combined_query = no_validation_needed_query.union(needs_validation_query)
        result = await db.execute(combined_query)
        valid_keys = set(result.scalars().all())
        logger.info(f"DEBUG claim_work_units: Input {len(work_unit_keys)} work units, found {len(valid_keys)} valid: {list(valid_keys)[:5]}")

        # Additional validation: filter out orphaned representation work units
        # where messages don't exist (regardless of session/workspace)
        orphaned_keys = set()
        for key in valid_keys:
            try:
                work_unit = parse_work_unit_key(key)
                if work_unit.task_type == "representation":
                    # Check if there are ANY linked messages (not just in expected session)
                    result = await db.execute(
                        select(func.count(models.Message.id))
                        .select_from(models.QueueItem)
                        .join(models.Message, models.QueueItem.message_id == models.Message.id)
                        .where(models.QueueItem.work_unit_key == key)
                        .where(~models.QueueItem.processed)
                    )
                    msg_count = result.scalar()
                    if msg_count == 0:
                        orphaned_keys.add(key)
                        logger.warning(
                            f"Marked orphan as errored: {key} - no linked messages found"
                        )
                        # Mark all queue items for this orphan as errored
                        await db.execute(
                            update(models.QueueItem)
                            .where(models.QueueItem.work_unit_key == key)
                            .where(~models.QueueItem.processed)
                            .values(
                                processed=True,
                                error="Orphaned work unit: no linked messages"
                            )
                        )
            except Exception as e:
                logger.warning(f"Error validating work unit {key}: {e}")
                orphaned_keys.add(key)
        
        valid_keys -= orphaned_keys

        logger.info(f"DEBUG claim_work_units: After orphaned filtering: {len(valid_keys)} valid, {len(orphaned_keys)} orphaned")

        if len(valid_keys) < len(work_unit_keys):
            total_filtered = len(work_unit_keys) - len(valid_keys)
            logger.info(
                f"Filtered out {total_filtered} work units ({len(orphaned_keys)} orphaned, {total_filtered - len(orphaned_keys)} empty)"
            )

        if not valid_keys:
            return {}

        values = [{"work_unit_key": key} for key in valid_keys]

        stmt = (
            insert(models.ActiveQueueSession)
            .values(values)
            .on_conflict_do_nothing()
            .returning(
                models.ActiveQueueSession.work_unit_key, models.ActiveQueueSession.id
            )
        )

        result = await db.execute(stmt)
        claimed_rows = result.all()
        claimed_mapping = {row[0]: row[1] for row in claimed_rows}
        logger.info(
            f"DEBUG claim_work_units FINAL: Inserted {len(claimed_mapping)} of {len(valid_keys)} work units into ActiveQueueSession: {list(claimed_mapping.keys())}"
        )
        return claimed_mapping

    async def polling_loop(self) -> None:
        """Main polling loop to find and process new work units"""
        logger.info("Starting polling loop")
        try:
            while not self.shutdown_event.is_set():
                if self.queue_empty_flag.is_set():
                    # logger.info("Queue empty flag set, waiting")
                    await asyncio.sleep(settings.DERIVER.POLLING_SLEEP_INTERVAL_SECONDS)
                    self.queue_empty_flag.clear()
                    continue

                # Check if we have capacity before querying
                if self.semaphore.locked():
                    # logger.info("All workers busy, waiting")
                    await asyncio.sleep(settings.DERIVER.POLLING_SLEEP_INTERVAL_SECONDS)
                    continue

                try:
                    await self.cleanup_stale_work_units()
                    # Periodically cleanup expired error tracker records
                    expired = self.error_tracker.cleanup_expired()
                    if expired:
                        logger.info(f"Cleaned up {expired} expired error tracker records")
                    claimed_work_units = await self.get_and_claim_work_units()
                    if claimed_work_units:
                        for work_unit_key, aqs_id in claimed_work_units.items():
                            # Create a new task for processing this work unit
                            if not self.shutdown_event.is_set():
                                # Track worker ownership
                                worker_id = self.create_worker_id()
                                self.track_worker_work_unit(
                                    worker_id, work_unit_key, aqs_id
                                )

                                task: Task[None] = asyncio.create_task(
                                    self.process_work_unit(work_unit_key, worker_id)
                                )
                                self.add_task(task)
                    else:
                        self.queue_empty_flag.set()
                        await asyncio.sleep(
                            settings.DERIVER.POLLING_SLEEP_INTERVAL_SECONDS
                        )
                except Exception as e:
                    logger.exception("Error in polling loop")
                    if settings.SENTRY.ENABLED:
                        sentry_sdk.capture_exception(e)
                    # Note: rollback is handled by tracked_db dependency
                    await asyncio.sleep(settings.DERIVER.POLLING_SLEEP_INTERVAL_SECONDS)
        finally:
            logger.info("Polling loop stopped")

    ######################
    # Queue Worker Logic #
    ######################

    async def _handle_processing_error(
        self,
        error: Exception,
        items: list[QueueItem],
        work_unit_key: str,
        context: str,
    ) -> None:
        """Handle processing errors using the retry/error tracker.

        Decision logic:
        1. Record the error in the in-memory tracker.
        2. If retries are exhausted AND backoff has expired:
           - Permanently mark the first queue item as errored
             (``processed=True, error=...``).
        3. Otherwise (retries remain or still in backoff):
           - Reset queue items to ``processed=False`` so they can be
             re-claimed after the backoff period.

        Args:
            error: The exception that occurred
            items: The queue items that were being processed
            work_unit_key: The work unit key for the queue items
            context: Context string describing what was being processed
        """
        error_msg = f"{error.__class__.__name__}: {str(error)}"

        # Record the error and get updated retry state
        record = self.error_tracker.record_error(work_unit_key, error_msg)

        logger.error(
            f"Error {context} for work unit {work_unit_key}: {error} "
            f"[retry {record.retry_count}/{self.error_tracker._max_retries}]",
            exc_info=True,
        )

        if settings.SENTRY.ENABLED:
            sentry_sdk.capture_exception(error)

        # Decide: retry or permanently fail?
        if self.error_tracker.should_escalate(work_unit_key):
            # Exhausted all retries AND backoff expired → permanent failure
            logger.warning(
                f"Work unit {work_unit_key} exhausted {record.retry_count} retries, "
                f"marking as permanently errored"
            )
            try:
                if items:
                    await self.mark_queue_item_as_errored(
                        items[0], work_unit_key, error_msg
                    )
            except Exception as mark_error:
                logger.error(
                    f"Failed to mark queue items as errored for work unit {work_unit_key}: {mark_error}",
                    exc_info=True,
                )
        else:
            # Retries remain → reset items to unprocessed for re-claim after backoff
            backoff_remaining = (record.backoff_until - datetime.now(timezone.utc)).total_seconds()
            logger.info(
                f"Work unit {work_unit_key} will be retried "
                f"(attempt {record.retry_count}/{self.error_tracker._max_retries}, "
                f"backoff {backoff_remaining:.1f}s)"
            )
            try:
                if items:
                    await self.reset_queue_items_for_retry(
                        items, work_unit_key, error_msg
                    )
            except Exception as reset_error:
                logger.error(
                    f"Failed to reset queue items for retry on work unit {work_unit_key}: {reset_error}",
                    exc_info=True,
                )

    async def process_work_unit(self, work_unit_key: str, worker_id: str) -> None:
        """Process all queue items for a specific work unit by routing to the correct handler.

        On success, clears the error tracker for this work unit. On failure,
        delegates to ``_handle_processing_error()`` which decides whether to
        retry (reset items to unprocessed) or permanently fail.

        When items are reset for retry, this worker releases ownership and
        stops processing the work unit. A future poll will re-claim it after
        the backoff period expires.
        """
        logger.info(f"Starting to process work unit {work_unit_key}")
        work_unit = parse_work_unit_key(work_unit_key)
        async with self.semaphore:
            queue_item_count = 0
            try:
                while not self.shutdown_event.is_set():
                    # Get worker ownership info for verification
                    ownership = self.worker_ownership.get(worker_id)
                    if not ownership or ownership.work_unit_key != work_unit_key:
                        logger.warning(
                            f"Worker {worker_id} lost ownership of work unit {work_unit_key}, stopping processing {work_unit_key}"
                        )
                        break

                    # Skip work units currently in backoff from a recent error
                    if self.error_tracker.is_backed_off(work_unit_key):
                        logger.info(
                            f"Work unit {work_unit_key} is in backoff, skipping"
                        )
                        break

                    try:
                        if work_unit.task_type == "representation":
                            (
                                messages_context,
                                items_to_process,
                                message_level_configuration,
                            ) = await self.get_queue_item_batch(
                                work_unit.task_type, work_unit_key, ownership.aqs_id
                            )
                            logger.info(
                                f"Worker {worker_id} retrieved {len(messages_context)} messages and {len(items_to_process)} queue items for work unit {work_unit_key} (AQS ID: {ownership.aqs_id})"
                            )
                            if not items_to_process:
                                logger.info(
                                    f"No more queue items to process for work unit {work_unit_key} for worker {worker_id}"
                                )
                                break

                            try:
                                # Extract observers from the payload (handle both old and new format)
                                payload = items_to_process[0].payload
                                observers = payload.get("observers")
                                if observers is None:
                                    # Legacy format: single observer string
                                    legacy_observer = payload.get("observer")
                                    if legacy_observer:
                                        observers = [legacy_observer]
                                    else:
                                        observers = []

                                queue_item_message_ids = [
                                    item.message_id
                                    for item in items_to_process
                                    if item.message_id is not None
                                ]
                                await process_representation_batch(
                                    messages_context,
                                    message_level_configuration,
                                    observers=observers,
                                    observed=work_unit.observed,
                                    queue_item_message_ids=queue_item_message_ids,
                                )
                                # Success — clear any prior error record
                                self.error_tracker.clear(work_unit_key)
                                await self.mark_queue_items_as_processed(
                                    items_to_process, work_unit_key
                                )
                                queue_item_count += len(items_to_process)
                            except Exception as e:
                                # Items may have been reset to unprocessed by
                                # _handle_processing_error, so release ownership
                                # and stop processing this work unit for now.
                                await self._handle_processing_error(
                                    e,
                                    items_to_process,
                                    work_unit_key,
                                    f"processing {work_unit.task_type} batch",
                                )
                                # Release ownership so the work unit can be
                                # re-claimed after backoff expires
                                break

                        else:
                            queue_item = await self.get_next_queue_item(
                                work_unit.task_type, work_unit_key, ownership.aqs_id
                            )
                            if not queue_item:
                                logger.info(
                                    f"No more queue items to process for work unit {work_unit_key} for worker {worker_id}"
                                )
                                break

                            try:
                                await process_item(queue_item)
                                # Success — clear any prior error record
                                self.error_tracker.clear(work_unit_key)
                                await self.mark_queue_items_as_processed(
                                    [queue_item], work_unit_key
                                )
                                queue_item_count += 1
                            except Exception as e:
                                await self._handle_processing_error(
                                    e,
                                    [queue_item],
                                    work_unit_key,
                                    "processing queue item",
                                )
                                # Release ownership so the work unit can be
                                # re-claimed after backoff expires
                                break

                    except Exception as e:
                        logger.error(
                            f"Error in processing loop for work unit {work_unit_key}: {e}",
                            exc_info=True,
                        )
                        if settings.SENTRY.ENABLED:
                            sentry_sdk.capture_exception(e)
                        break

                    # Check for shutdown after processing each batch
                    if self.shutdown_event.is_set():
                        logger.info(
                            "Shutdown requested, stopping processing for work unit %s",
                            work_unit_key,
                        )
                        break

            finally:
                # Remove work unit from active_queue_sessions when done
                ownership: WorkerOwnership | None = self.worker_ownership.get(worker_id)
                if ownership and ownership.work_unit_key == work_unit_key:
                    removed = await self._cleanup_work_unit(
                        ownership.aqs_id, work_unit_key
                    )
                else:
                    removed = False

                self.untrack_worker_work_unit(worker_id, work_unit_key)
                if removed and queue_item_count > 0:
                    # Only publish webhook if we actually removed an active session
                    try:
                        if (
                            work_unit.task_type in ["representation", "summary"]
                            and work_unit.workspace_name is not None
                        ):
                            logger.info(
                                f"Publishing queue.empty event for {work_unit_key} in workspace {work_unit.workspace_name}"
                            )
                            await publish_webhook_event(
                                QueueEmptyEvent(
                                    workspace_id=work_unit.workspace_name,
                                    queue_type=work_unit.task_type,
                                    session_id=work_unit.session_name,
                                    observer=work_unit.observer,
                                    observed=work_unit.observed,
                                )
                            )
                    except Exception:
                        logger.exception("Error triggering queue_empty webhook")
                else:
                    logger.info(
                        f"Work unit {work_unit_key} already cleaned up by another worker, skipping webhook"
                    )

    @sentry_sdk.trace
    async def get_next_queue_item(
        self, task_type: str, work_unit_key: str, aqs_id: str
    ) -> QueueItem | None:
        """Get the next queue item to process for a specific work unit."""
        if task_type == "representation":
            raise ValueError(
                "representation tasks are not supported for get_next_queue_item"
            )
        async with tracked_db("get_next_queue_item") as db:
            # ActiveQueueSession conditions for worker ownership verification
            aqs_conditions = [
                models.ActiveQueueSession.work_unit_key == work_unit_key,
                models.ActiveQueueSession.id == aqs_id,
            ]

            query = (
                select(models.QueueItem)
                .join(
                    models.ActiveQueueSession,
                    models.QueueItem.work_unit_key
                    == models.ActiveQueueSession.work_unit_key,
                )
                .where(models.QueueItem.work_unit_key == work_unit_key)
                .where(~models.QueueItem.processed)
                .where(*aqs_conditions)
                .order_by(models.QueueItem.id)
                .limit(1)
            )
            result = await db.execute(query)
            queue_item = result.scalar_one_or_none()

            # Important: commit to avoid tracked_db's rollback expiring the instance
            # We rely on expire_on_commit=False to keep attributes accessible post-close
            await db.commit()
            return queue_item

    @sentry_sdk.trace
    async def get_queue_item_batch(
        self,
        task_type: str,
        work_unit_key: str,
        aqs_id: str,
    ) -> tuple[list[models.Message], list[QueueItem], ResolvedConfiguration | None]:
        """
        Batch processing for representation and agent tasks.
        Returns a tuple of (messages_context, items_to_process, configuration).
        - messages_context: unique Message rows (conversation turns) forming the context window
        - items_to_process: QueueItems for the current work_unit_key within that window
        - configuration: Resolved configuration for the batch
        """
        if task_type != "representation":
            raise ValueError(
                f"{task_type} tasks are not supported for get_queue_item_batch"
            )

        batch_max_tokens = settings.DERIVER.REPRESENTATION_BATCH_MAX_TOKENS

        async with tracked_db("get_queue_item_batch") as db:
            # For batch tasks, get messages based on token limit.
            # Step 1: Parse work_unit_key to get session context and focused sender
            parsed_key = parse_work_unit_key(work_unit_key)

            # Verify worker still owns the work_unit_key
            ownership_check = await db.execute(
                select(models.ActiveQueueSession.id)
                .where(models.ActiveQueueSession.work_unit_key == work_unit_key)
                .where(models.ActiveQueueSession.id == aqs_id)
            )
            if not ownership_check.scalar_one_or_none():
                # Worker lost ownership, return empty
                await db.commit()
                return [], [], None

            # Step 2: Build a single SQL query that:
            # 1. Finds the earliest unprocessed message for this work_unit_key
            # 2. Optionally includes the preceding message if from a different peer (for context)
            # 3. Gets ALL messages from that point forward (for conversational context)
            # 4. Tracks cumulative tokens and focused sender position
            # 5. Returns empty if focused sender is beyond token limit
            # 6. Otherwise returns messages up to token limit + first focused sender message

            # Find the minimum message_id with an unprocessed queue item across the session
            min_unprocessed_message_id_subq = (
                select(func.min(models.Message.id))
                .select_from(models.QueueItem)
                .join(
                    models.Message,
                    models.QueueItem.message_id == models.Message.id,
                )
                .where(~models.QueueItem.processed)
                .where(models.Message.session_name == parsed_key.session_name)
                .where(models.Message.workspace_name == parsed_key.workspace_name)
                .where(models.QueueItem.work_unit_key == work_unit_key)
                .scalar_subquery()
            )

            # Find the immediately preceding message ID (the one right before min_unprocessed)
            immediately_preceding_id_subq = (
                select(func.max(models.Message.id))
                .where(models.Message.session_name == parsed_key.session_name)
                .where(models.Message.workspace_name == parsed_key.workspace_name)
                .where(models.Message.id < min_unprocessed_message_id_subq)
                .scalar_subquery()
            )

            # Only include the preceding message if it's from a different peer than observed
            # This provides conversational context (e.g., the question that prompted the response)
            preceding_message_id_subq = (
                select(models.Message.id)
                .where(models.Message.id == immediately_preceding_id_subq)
                .where(models.Message.peer_name != parsed_key.observed)
                .scalar_subquery()
            )

            # Determine the effective start: preceding message if it qualifies, else min_unprocessed
            # We use COALESCE to fall back to min_unprocessed if no preceding message qualifies
            effective_start_id = func.coalesce(
                preceding_message_id_subq, min_unprocessed_message_id_subq
            )

            # Build CTE with ALL messages starting from effective_start_id
            # This includes the preceding context message (if any) and interleaving messages
            cte = (
                select(
                    models.Message.id.label("message_id"),
                    models.Message.token_count.label("token_count"),
                    models.Message.peer_name.label("peer_name"),
                    func.sum(models.Message.token_count)
                    .over(order_by=models.Message.id)
                    .label("cumulative_token_count"),
                )
                .where(models.Message.session_name == parsed_key.session_name)
                .where(models.Message.workspace_name == parsed_key.workspace_name)
                .where(models.Message.id >= effective_start_id)
                .order_by(models.Message.id)
                .cte()
            )

            allowed_condition = (
                (cte.c.cumulative_token_count <= batch_max_tokens)
                | (
                    cte.c.message_id == min_unprocessed_message_id_subq
                )  # always include the first unprocessed message
            )

            query = (
                select(models.Message, models.QueueItem)
                .select_from(cte)
                .join(models.Message, models.Message.id == cte.c.message_id)
                .outerjoin(
                    models.QueueItem,
                    and_(
                        models.QueueItem.work_unit_key == work_unit_key,
                        ~models.QueueItem.processed,
                        models.QueueItem.message_id == models.Message.id,
                    ),
                )
                .where(allowed_condition)
                .order_by(models.Message.id, models.QueueItem.id)
            )

            result = await db.execute(query)
            rows = result.all()
            if not rows:
                await db.commit()
                return [], [], None

            messages_context: list[models.Message] = []
            items_to_process: list[QueueItem] = []
            seen_messages: set[int] = set()
            for m, qi in rows:
                if m.id not in seen_messages:
                    messages_context.append(m)
                    seen_messages.add(m.id)
                if qi is not None:
                    items_to_process.append(qi)

            if items_to_process:
                # Enforce homogeneous peer_card_config in the batch
                # We stop collecting items as soon as we encounter a different configuration
                payload = items_to_process[0].payload

                raw_config = payload.get("configuration")
                if raw_config is None:
                    resolved_config = None
                else:
                    resolved_config = ResolvedConfiguration.model_validate(raw_config)

                valid_items: list[QueueItem] = []
                for item in items_to_process:
                    item_raw_config = item.payload.get("configuration")
                    if item_raw_config is None:
                        item_config = None
                    else:
                        item_config = ResolvedConfiguration.model_validate(
                            item_raw_config
                        )
                    if item_config != resolved_config:
                        break
                    valid_items.append(item)
                items_to_process = valid_items
            else:
                resolved_config = None

            if items_to_process:
                max_queue_item_message_id = max(
                    [
                        qi.message_id
                        for qi in items_to_process
                        if qi.message_id is not None
                    ]
                )
                messages_context = [  # remove any messages that are after the last message_id from queue items
                    m for m in messages_context if m.id <= max_queue_item_message_id
                ]

            await db.commit()

            return messages_context, items_to_process, resolved_config

    async def mark_queue_items_as_processed(
        self, items: list[QueueItem], work_unit_key: str
    ) -> None:
        if not items:
            return
        async with tracked_db("process_queue_item_batch") as db:
            work_unit = parse_work_unit_key(work_unit_key)
            item_ids = [item.id for item in items]
            await db.execute(
                update(models.QueueItem)
                .where(models.QueueItem.id.in_(item_ids))
                .where(models.QueueItem.work_unit_key == work_unit_key)
                .values(processed=True)
            )
            await db.execute(
                update(models.ActiveQueueSession)
                .where(models.ActiveQueueSession.work_unit_key == work_unit_key)
                .values(last_updated=func.now())
            )
            await db.commit()

            if (
                work_unit.task_type in ["representation", "summary"]
                and work_unit.workspace_name is not None
                and settings.METRICS.ENABLED
            ):
                prometheus_metrics.record_deriver_queue_item(
                    count=len(items),
                    workspace_name=work_unit.workspace_name,
                    task_type=work_unit.task_type,
                )

    async def reset_queue_items_for_retry(
        self,
        items: list[QueueItem],
        work_unit_key: str,
        error: str,
    ) -> None:
        """Reset queue items to unprocessed so they can be re-claimed later.

        Used when a transient error occurs and retries remain. The queue
        item's ``error`` field is updated with the latest error for
        observability, but ``processed`` is set to False so the item
        will be picked up again on a future poll.
        """
        if not items:
            return
        async with tracked_db("reset_queue_items_for_retry") as db:
            item_ids = [item.id for item in items]
            await db.execute(
                update(models.QueueItem)
                .where(models.QueueItem.id.in_(item_ids))
                .where(models.QueueItem.work_unit_key == work_unit_key)
                .values(processed=False, error=error[:65535])
            )
            # Keep ActiveQueueSession alive so the work unit stays "in progress"
            await db.execute(
                update(models.ActiveQueueSession)
                .where(models.ActiveQueueSession.work_unit_key == work_unit_key)
                .values(last_updated=func.now())
            )
            await db.commit()

    async def mark_queue_item_as_errored(
        self, item: QueueItem, work_unit_key: str, error: str
    ) -> None:
        """Mark queue item as processed with an error"""
        if not item:
            return
        async with tracked_db("mark_queue_item_as_errored") as db:
            await db.execute(
                update(models.QueueItem)
                .where(models.QueueItem.id == item.id)
                .where(models.QueueItem.work_unit_key == work_unit_key)
                .values(processed=True, error=error[:65535])  # Truncate to TEXT limit
            )
            await db.execute(
                update(models.ActiveQueueSession)
                .where(models.ActiveQueueSession.work_unit_key == work_unit_key)
                .values(last_updated=func.now())
            )
            await db.commit()

    async def _cleanup_work_unit(
        self,
        aqs_id: str,
        work_unit_key: str,
    ) -> bool:
        """
        Clean up a specific work unit session by both work_unit_key and AQS ID.
        """
        async with tracked_db("cleanup_work_unit") as db:
            result = cast(
                CursorResult[Any],
                await db.execute(
                    delete(models.ActiveQueueSession)
                    .where(models.ActiveQueueSession.id == aqs_id)
                    .where(models.ActiveQueueSession.work_unit_key == work_unit_key)
                ),
            )
            await db.commit()
            return result.rowcount > 0


async def main():
    logger.info("Starting queue manager")

    try:
        await init_cache()
    except Exception as e:
        logger.warning(
            "Error initializing cache in queue manager; proceeding without cache: %s", e
        )

    manager = QueueManager()
    try:
        await manager.initialize()
    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        sentry_sdk.capture_exception(e)
    finally:
        await close_cache()
        logger.info("Main function exiting")
