"""WRR (Weighted Round-Robin) metrics module.

This module provides logging and Prometheus-style metrics for monitoring
WRR quota distribution, task type queries, idle fill behavior, and
starvation prevention events.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Callable

from prometheus_client import Counter, Gauge, Histogram

from src.config import settings

logger = logging.getLogger(__name__)


class WRRTaskTypes(str, Enum):
    """Task types for WRR metrics labeling."""

    REPRESENTATION = "representation"
    SUMMARY = "summary"
    WEBHOOK = "webhook"
    DREAM = "dream"
    RECONCILER = "reconciler"
    IDLE_FILL = "idle_fill"


class IdleFillStrategies(str, Enum):
    """Idle fill strategies for metrics labeling."""

    OLDEST_FIRST = "oldest_first"
    RANDOM = "random"


# Prometheus-style metrics (only initialized if metrics enabled)
_wrr_metrics_initialized = False

# Gauges (current state)
wrr_quota_gauge: Gauge | None = None
wrr_task_allocation_gauge: Gauge | None = None

# Counters (cumulative events)
wrr_quota_counter: Counter | None = None
wrr_task_query_counter: Counter | None = None
wrr_task_returned_counter: Counter | None = None
wrr_idle_fill_counter: Counter | None = None
wrr_starvation_prevention_counter: Counter | None = None

# Histograms (distributions)
wrr_quota_diff_histogram: Histogram | None = None
wrr_fill_efficiency_histogram: Histogram | None = None


def _initialize_metrics() -> None:
    """Initialize Prometheus metrics lazily."""
    global _wrr_metrics_initialized
    global wrr_quota_gauge, wrr_task_allocation_gauge
    global wrr_quota_counter, wrr_task_query_counter, wrr_task_returned_counter
    global wrr_idle_fill_counter, wrr_starvation_prevention_counter
    global wrr_quota_diff_histogram, wrr_fill_efficiency_histogram

    if _wrr_metrics_initialized:
        return

    metrics_namespace = settings.METRICS.NAMESPACE
    subsystem = "wrr"

    # Gauges - current values that can go up and down
    wrr_quota_gauge = Gauge(
        "wrr_quota",
        "Current WRR quota allocation per task type",
        ["namespace", "task_type"],
        namespace=metrics_namespace,
        subsystem=subsystem,
    )

    wrr_task_allocation_gauge = Gauge(
        "wrr_task_allocation",
        "Current task allocation per task type",
        ["namespace", "task_type"],
        namespace=metrics_namespace,
        subsystem=subsystem,
    )

    # Counters - monotonically increasing values
    wrr_quota_counter = Counter(
        "wrr_quota_calculations_total",
        "Total number of WRR quota calculations performed",
        ["namespace"],
        namespace=metrics_namespace,
        subsystem=subsystem,
    )

    wrr_task_query_counter = Counter(
        "wrr_task_queries_total",
        "Total number of task type queries",
        ["namespace", "task_type"],
        namespace=metrics_namespace,
        subsystem=subsystem,
    )

    wrr_task_returned_counter = Counter(
        "wrr_tasks_returned_total",
        "Total number of work units returned from queries",
        ["namespace", "task_type"],
        namespace=metrics_namespace,
        subsystem=subsystem,
    )

    wrr_idle_fill_counter = Counter(
        "wrr_idle_fill_total",
        "Total number of idle fill operations",
        ["namespace", "strategy"],
        namespace=metrics_namespace,
        subsystem=subsystem,
    )

    wrr_starvation_prevention_counter = Counter(
        "wrr_starvation_prevention_total",
        "Total number of starvation prevention activations",
        ["namespace", "task_type", "reason"],
        namespace=metrics_namespace,
        subsystem=subsystem,
    )

    # Histograms - distributions of values
    wrr_quota_diff_histogram = Histogram(
        "wrr_quota_requested_diff",
        "Difference between requested and returned work units",
        ["namespace", "task_type"],
        buckets=[0, 1, 2, 5, 10, 25, 50, 100],
        namespace=metrics_namespace,
        subsystem=subsystem,
    )

    wrr_fill_efficiency_histogram = Histogram(
        "wrr_fill_efficiency_ratio",
        "Ratio of filled slots to requested slots for idle_fill",
        ["namespace", "strategy"],
        buckets=[0.0, 0.25, 0.5, 0.75, 1.0],
        namespace=metrics_namespace,
        subsystem=subsystem,
    )

    _wrr_metrics_initialized = True


def _should_record_metrics() -> bool:
    """Check if metrics should be recorded based on settings."""
    # Check global metrics enabled
    if not settings.METRICS.ENABLED:
        return False
    # Check WRR-specific metrics enabled (if field exists)
    wrr_settings = getattr(settings, 'DERIVER', None)
    if wrr_settings and hasattr(wrr_settings, 'WRR'):
        wrr_metrics_enabled = getattr(wrr_settings.WRR, 'METRICS_ENABLED', None)
        if wrr_metrics_enabled is False:
            return False
    return True


def _with_namespace(metric_call: Callable[[], None]) -> None:
    """Wrapper to add namespace to metric labels."""
    if not _should_record_metrics():
        return
    try:
        _initialize_metrics()
        metric_call()
    except Exception as e:
        logger.debug(f"Failed to record metric: {e}")


def record_quota_calculation(
    quotas: dict[str, int],
    task_type_allocation: dict[str, int],
) -> None:
    """Record WRR quota calculation results.

    Args:
        quotas: Calculated quotas per task type
        task_type_allocation: Actual work units allocated per task type
    """
    namespace = settings.METRICS.NAMESPACE

    # Always log at debug level
    logger.debug(
        f"WRR quota calculation: quotas={quotas}, "
        f"allocation={task_type_allocation}"
    )

    if not _should_record_metrics():
        return

    _initialize_metrics()

    # Record counter
    try:
        wrr_quota_counter.labels(namespace=namespace).inc()
    except Exception:
        pass

    # Update gauges for each task type
    for task_type, quota in quotas.items():
        try:
            wrr_quota_gauge.labels(
                namespace=namespace,
                task_type=task_type,
            ).set(quota)
        except Exception:
            pass

        # Set actual allocation
        actual = task_type_allocation.get(task_type, 0)
        try:
            wrr_task_allocation_gauge.labels(
                namespace=namespace,
                task_type=task_type,
            ).set(actual)
        except Exception:
            pass

        # Record difference histogram
        diff = quota - actual
        try:
            wrr_quota_diff_histogram.labels(
                namespace=namespace,
                task_type=task_type,
            ).observe(diff)
        except Exception:
            pass


def record_task_type_query(
    task_type: str,
    requested: int,
    returned: int,
) -> None:
    """Record task type query results.

    Args:
        task_type: The task type that was queried
        requested: Number of work units requested
        returned: Number of work units actually returned
    """
    namespace = settings.METRICS.NAMESPACE

    # Always log at debug level
    logger.debug(
        f"WRR task query: task_type={task_type}, "
        f"requested={requested}, returned={returned}"
    )

    if not _should_record_metrics():
        return

    _initialize_metrics()

    # Counter increments
    try:
        wrr_task_query_counter.labels(
            namespace=namespace,
            task_type=task_type,
        ).inc()
        wrr_task_returned_counter.labels(
            namespace=namespace,
            task_type=task_type,
        ).inc(returned)
    except Exception:
        pass

    # Record difference between requested and returned
    diff = requested - returned
    try:
        wrr_quota_diff_histogram.labels(
            namespace=namespace,
            task_type=task_type,
        ).observe(diff)
    except Exception:
        pass

    # Log if significantly under-requested (potential starvation)
    if requested > 0 and returned == 0:
        logger.warning(
            f"WRR starvation warning: {task_type} requested {requested} "
            f"but returned 0 work units"
        )


def record_idle_fill(
    filled_count: int,
    requested_count: int,
    strategy: str,
) -> None:
    """Record idle fill operation results.

    Args:
        filled_count: Number of slots actually filled
        requested_count: Number of slots requested
        strategy: The idle fill strategy used
    """
    namespace = settings.METRICS.NAMESPACE

    # Always log
    efficiency = filled_count / requested_count if requested_count > 0 else 0.0
    logger.debug(
        f"WRR idle fill: filled={filled_count}, "
        f"requested={requested_count}, strategy={strategy}, "
        f"efficiency={efficiency:.2%}"
    )

    if not _should_record_metrics():
        return

    _initialize_metrics()

    # Counter increment
    try:
        wrr_idle_fill_counter.labels(
            namespace=namespace,
            strategy=strategy,
        ).inc(filled_count)
    except Exception:
        pass

    # Efficiency histogram
    if requested_count > 0:
        try:
            wrr_fill_efficiency_histogram.labels(
                namespace=namespace,
                strategy=strategy,
            ).observe(filled_count / requested_count)
        except Exception:
            pass

    # Log efficiency warnings
    if requested_count > 0 and filled_count < requested_count:
        logger.info(
            f"WRR idle fill efficiency: {filled_count}/{requested_count} "
            f"({efficiency:.1%}) using {strategy} strategy"
        )


def record_starvation_prevention(
    task_type: str,
    reason: str,
    details: dict | None = None,
) -> None:
    """Record starvation prevention activation.

    Args:
        task_type: The task type being protected
        reason: Why starvation prevention was activated
        details: Optional additional context
    """
    namespace = settings.METRICS.NAMESPACE

    # Always log at warning level
    detail_str = f" details={details}" if details else ""
    logger.warning(
        f"WRR starvation prevention: task_type={task_type}, "
        f"reason={reason}{detail_str}"
    )

    if not _should_record_metrics():
        return

    _initialize_metrics()

    # Counter increment
    try:
        wrr_starvation_prevention_counter.labels(
            namespace=namespace,
            task_type=task_type,
            reason=reason,
        ).inc()
    except Exception as e:
        logger.debug(f"Failed to record starvation prevention metric: {e}")


# Convenience function for batch metrics recording
def record_wrr_poll_summary(
    quotas: dict[str, int],
    allocations: dict[str, int],
    idle_fill_requested: int,
    idle_fill_filled: int,
    strategy: str,
) -> None:
    """Record a summary of a complete WRR poll cycle.

    This is a convenience function that calls the individual metric
    recording functions in the correct order.

    Args:
        quotas: Calculated quotas per task type
        allocations: Actual work units allocated per task type
        idle_fill_requested: Number of idle_fill slots requested
        idle_fill_filled: Number of idle_fill slots actually filled
        strategy: The idle fill strategy used
    """
    # Record quota calculation
    record_quota_calculation(quotas, allocations)

    # Record task type queries
    for task_type in quotas:
        if task_type == "idle_fill":
            continue
        requested = quotas.get(task_type, 0)
        returned = allocations.get(task_type, 0)
        if requested > 0 or returned > 0:
            record_task_type_query(task_type, requested, returned)

    # Record idle fill
    if idle_fill_requested > 0:
        record_idle_fill(idle_fill_filled, idle_fill_requested, strategy)

    # Log summary
    total_requested = sum(quotas.values())
    total_allocated = sum(allocations.values())
    logger.debug(
        f"WRR poll summary: requested={total_requested}, "
        f"allocated={total_allocated}, "
        f"efficiency={total_allocated/total_requested:.1%} "
        if total_requested > 0
        else "WRR poll summary: no capacity requested"
    )
