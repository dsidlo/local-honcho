"""Weighted Round-Robin (WRR) queue query module.

This module provides database query functions for the WRR scheduling algorithm,
including per-task-type querying, idle fill capabilities, and quota calculation.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import models
from src.config import settings

logger = logging.getLogger(__name__)


def calculate_wrr_quotas(
    available_workers: int,
    weights: dict[str, float],
    min_slots: dict[str, int],
    max_slots: dict[str, int | None],
) -> dict[str, int]:
    """Calculate per-task-type quotas using Weighted Round-Robin with minimum guarantees.

    Algorithm:
    1. Initialize all quotas to 0
    2. Allocate minimum guarantees fairly (capped by max_slots and available_workers)
    3. Distribute remaining capacity proportionally by weights
    4. Respect max_slots during distribution
    5. Fill any remaining slots with tasks that have capacity

    Args:
        available_workers: Total workers available
        weights: Dict[task_type, weight] - must sum to 1.0
        min_slots: Dict[task_type, minimum_guaranteed_slots]
        max_slots: Dict[task_type, max_slots_or_None]

    Returns:
        Dict[task_type, allocated_quota]
    """
    if available_workers <= 0:
        return {task_type: 0 for task_type in weights}

    # Initialize all quotas to 0
    quotas: dict[str, int] = {task_type: 0 for task_type in weights}
    remaining = available_workers

    # Phase 1: Allocate minimum guarantees (fairly)
    # Get types that need minimum guarantees (excluding idle_fill which is opportunistic)
    types_needing_min = [
        tt for tt in weights
        if tt != "idle_fill" and min_slots.get(tt, 0) > 0
    ]

    if types_needing_min:
        # Calculate total minimum slots needed (respecting max_slots)
        effective_mins = {}
        for task_type in types_needing_min:
            minimum = min_slots.get(task_type, 0)
            current_max = max_slots.get(task_type)
            if current_max is not None:
                minimum = min(minimum, current_max)
            effective_mins[task_type] = minimum

        total_min_needed = sum(effective_mins.values())

        if total_min_needed <= available_workers:
            # Easy case: allocate all minimums
            for task_type, minimum in effective_mins.items():
                quotas[task_type] = minimum
                remaining -= minimum
        else:
            # Hard case: need to fairly distribute minimums
            # Strategy: prioritize by highest minimum first, then round-robin for ties
            sorted_types = sorted(
                effective_mins.keys(),
                key=lambda tt: (-effective_mins[tt], tt)  # Highest min first, then by name
            )

            # First pass: try to allocate proportional to minimums
            # using ceiling allocation to ensure we hit the min(min, workers) target
            while remaining > 0 and any(
                quotas[tt] < effective_mins[tt] for tt in sorted_types
            ):
                made_progress = False
                for task_type in sorted_types:
                    if remaining <= 0:
                        break
                    target = effective_mins[task_type]
                    current = quotas[task_type]
                    if current < target:
                        # Calculate how much we still need
                        needed = target - current
                        # Take at least 1, but try to satisfy more if we have capacity
                        # and this type has higher minimum
                        take = min(needed, remaining)
                        if take > 0:
                            quotas[task_type] += take
                            remaining -= take
                            made_progress = True
                if not made_progress:
                    break

    if remaining <= 0:
        return quotas

    # Phase 2: Distribute remaining by weights
    def can_accept_more(task_type: str) -> bool:
        current = quotas.get(task_type, 0)
        max_cap = max_slots.get(task_type)
        return max_cap is None or current < max_cap

    # Distribute one slot at a time to available types
    for _ in range(remaining):
        available = [tt for tt in weights if can_accept_more(tt)]

        if not available:
            break

        total_weight = sum(weights[tt] for tt in available)

        if total_weight == 0:
            best_type = available[0]
        else:
            # Find the type with the largest deficit compared to target
            best_type = available[0]
            best_deficit = -float('inf')

            for tt in available:
                target = (weights[tt] / total_weight) * available_workers
                current = quotas[tt]
                deficit = target - current

                if deficit > best_deficit:
                    best_deficit = deficit
                    best_type = tt
                elif deficit == best_deficit:
                    # Tie-breaker: smallest current allocation
                    if quotas[tt] < quotas[best_type]:
                        best_type = tt

        quotas[best_type] = quotas[best_type] + 1

    return quotas


async def query_task_type_work_units(
    db: AsyncSession,
    task_type: str,
    limit: int,
) -> list[str]:
    """Query available work units for a specific task type.

    SECURITY NOTE: task_type is expected to come from configuration, not user input.
    The task_type value is validated against a whitelist (representation, webhook,
    dream, reconciler, summary). Unknown task types return empty list with a warning.

    This is a dispatcher function that routes to the appropriate
    task-specific query function based on task_type.

    Args:
        db: Database session
        task_type: The task type to query (e.g., "representation", "webhook")
        limit: Maximum number of work units to return

    Returns:
        List of work_unit_keys for the specified task type
    """
    if task_type == "representation":
        return await _query_representation_work_units(db, limit)
    elif task_type == "idle_fill":
        return []  # idle_fill is handled separately in main loop
    elif task_type in ("webhook", "dream", "reconciler", "summary"):
        return await _query_standard_work_units(db, task_type, limit)
    else:
        logger.warning(f"Unknown task type: {task_type}")
        return []


async def _query_standard_work_units(
    db: AsyncSession,
    task_type: str,
    limit: int,
) -> list[str]:
    """Query webhook, dream, reconciler, or summary work units.

    These task types do not require message validation and are ordered
    by age (oldest first) to prevent starvation.

    Args:
        db: Database session
        task_type: The task type to query
        limit: Maximum number of work units to return

    Returns:
        List of work_unit_keys for the specified task type
    """
    query = (
        select(models.QueueItem.work_unit_key)
        .where(~models.QueueItem.processed)
        .where(
            models.QueueItem.work_unit_key.startswith(f"{task_type}:")
        )
        .where(
            ~select(models.ActiveQueueSession.id)
            .where(
                models.ActiveQueueSession.work_unit_key
                == models.QueueItem.work_unit_key
            )
            .exists()
        )
        .group_by(models.QueueItem.work_unit_key)
        .having(func.count(models.QueueItem.id) > 0)
        .order_by(func.min(models.QueueItem.created_at))
        .limit(limit)
    )

    result = await db.execute(query)
    return [row[0] for row in result.all()]


async def _query_representation_work_units(
    db: AsyncSession,
    limit: int,
) -> list[str]:
    """Query representation work units with token batching threshold.

    Representation tasks are ordered by token count (descending) to maximize
    batch efficiency, NOT by age. This is an intentional trade-off for
    batch processing efficiency.

    If no work units meet the token threshold, includes an anti-starvation
    fallback that processes tasks older than 1 hour even if below threshold.

    Args:
        db: Database session
        limit: Maximum number of work units to return

    Returns:
        List of work_unit_keys for representation tasks
    """
    representation_prefix = "representation:"
    batch_max_tokens = settings.DERIVER.REPRESENTATION_BATCH_MAX_TOKENS

    # Token statistics subquery
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
        .where(
            models.QueueItem.work_unit_key.startswith(representation_prefix)
        )
        .group_by(models.QueueItem.work_unit_key)
        .subquery()
    )

    # Base query for representation units with token threshold
    query = (
        select(token_stats_subq.c.work_unit_key)
        .where(
            token_stats_subq.c.total_tokens > 0,  # Filter out zero-token tasks
            ~select(models.ActiveQueueSession.id)
            .where(
                models.ActiveQueueSession.work_unit_key
                == token_stats_subq.c.work_unit_key
            )
            .exists()
        )
        .order_by(token_stats_subq.c.total_tokens.desc())
        .limit(limit)
    )

    # Apply token threshold filter (skip if FLUSH_ENABLED)
    if not settings.DERIVER.FLUSH_ENABLED and batch_max_tokens > 0:
        query = query.where(
            func.coalesce(token_stats_subq.c.total_tokens, 0) >= batch_max_tokens
        )

    result = await db.execute(query)
    rows = result.all()

    # Anti-starvation fallback: if no results and tasks are ancient (> 1 hour old)
    if not rows and not settings.DERIVER.FLUSH_ENABLED and batch_max_tokens > 0:
        # Calculate anti-starvation threshold (1 hour ago)
        anti_starvation_cutoff = datetime.now(timezone.utc) - timedelta(hours=1)

        fallback_query = (
            select(token_stats_subq.c.work_unit_key)
            .join(
                models.QueueItem,
                models.QueueItem.work_unit_key == token_stats_subq.c.work_unit_key,
            )
            .where(
                token_stats_subq.c.total_tokens > 0,  # Has some tokens
                models.QueueItem.created_at < anti_starvation_cutoff,  # Very old
            )
            .where(
                ~select(models.ActiveQueueSession.id)
                .where(
                    models.ActiveQueueSession.work_unit_key
                    == token_stats_subq.c.work_unit_key
                )
                .exists()
            )
            .order_by(models.QueueItem.created_at)  # oldest first
            .limit(limit)
        )

        result = await db.execute(fallback_query)
        rows = result.all()

        if rows:
            logger.warning(
                f"WRR: Processing {len(rows)} representation tasks below "
                f"token threshold due to age (anti-starvation)"
            )

    return [row[0] for row in rows]


async def query_idle_fill_work_units(
    db: AsyncSession,
    limit: int,
    exclude_work_units: set[str] | None = None,
    strategy: str = "oldest_first",
) -> list[str]:
    """Query oldest available work units of any type.

    This is used to fill unused worker capacity with tasks regardless of
    their type. For representation tasks, respects the token threshold
    unless anti-starvation conditions are met.

    Args:
        db: Database session
        limit: Maximum number of work units to return
        exclude_work_units: Set of work_unit_keys to exclude
        strategy: Fill strategy (only "oldest_first" supported currently)

    Returns:
        List of work_unit_keys across all task types, ordered by age
    """
    if strategy != "oldest_first":
        logger.warning(f"Unknown idle_fill strategy '{strategy}', using 'oldest_first'")

    # Build subquery for work unit ages
    age_subq = (
        select(
            models.QueueItem.work_unit_key,
            func.min(models.QueueItem.created_at).label("oldest_created"),
        )
        .where(~models.QueueItem.processed)
        .where(
            ~select(models.ActiveQueueSession.id)
            .where(
                models.ActiveQueueSession.work_unit_key
                == models.QueueItem.work_unit_key
            )
            .exists()
        )
        .group_by(models.QueueItem.work_unit_key)
        .subquery()
    )

    # Base query for oldest work units
    query = select(age_subq.c.work_unit_key).order_by(age_subq.c.oldest_created)

    # Exclude already-claimed work units
    if exclude_work_units:
        query = query.where(
            age_subq.c.work_unit_key.not_in(exclude_work_units)
        )

    # Apply token threshold for representation tasks
    # This is done by excluding representation work units that don't meet threshold
    if not settings.DERIVER.FLUSH_ENABLED:
        batch_max_tokens = settings.DERIVER.REPRESENTATION_BATCH_MAX_TOKENS

        # Build representation token subquery
        rep_token_subq = (
            select(
                models.QueueItem.work_unit_key.label("rep_key"),
                func.sum(models.Message.token_count).label("token_sum"),
            )
            .join(
                models.Message,
                models.QueueItem.message_id == models.Message.id,
            )
            .where(~models.QueueItem.processed)
            .where(
                models.QueueItem.work_unit_key.startswith("representation:")
            )
            .group_by(models.QueueItem.work_unit_key)
            .having(
                func.sum(models.Message.token_count) < batch_max_tokens
            )
            .subquery()
        )

        # Exclude representation tasks below threshold unless they're old
        anti_starvation_cutoff = datetime.now(timezone.utc) - timedelta(hours=1)

        # Get old representation tasks (allowed for anti-starvation)
        old_rep_subq = (
            select(models.QueueItem.work_unit_key.label("old_rep_key"))
            .where(
                models.QueueItem.work_unit_key.startswith("representation:"),
                models.QueueItem.created_at < anti_starvation_cutoff,
                ~models.QueueItem.processed,
            )
            .subquery()
        )

        # Exclude only representation tasks below threshold that are NOT old
        query = query.where(
            ~(
                age_subq.c.work_unit_key.in_(
                    select(rep_token_subq.c.rep_key)
                )
                & ~age_subq.c.work_unit_key.in_(
                    select(old_rep_subq.c.old_rep_key)
                )
            )
        )

    query = query.limit(limit)

    result = await db.execute(query)
    return [row[0] for row in result.all()]
