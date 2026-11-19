"""Reset operations service for quick revert."""
import logging
from typing import AsyncGenerator, Optional, Dict
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class ResetOperation(Enum):
    """Types of reset operations."""
    QUICK = "quick"


class ResetStatus(Enum):
    """Status of a reset operation."""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ResetService:
    """Service for managing reset operations."""

    def __init__(self):
        self._current_operation: Optional[ResetOperation] = None
        self._current_status: ResetStatus = ResetStatus.IDLE
        self._operation_start_time: Optional[datetime] = None

    @property
    def is_running(self) -> bool:
        """Check if an operation is currently running."""
        return self._current_status == ResetStatus.RUNNING

    def get_status(self) -> Dict:
        """Get the current status of reset operations."""
        return {
            "status": self._current_status.value,
            "operation": self._current_operation.value if self._current_operation else None,
            "start_time": self._operation_start_time.isoformat() if self._operation_start_time else None,
            "is_running": self.is_running
        }

    async def run_quick_revert(self) -> AsyncGenerator[str, None]:
        """
        Quickly revert all tracked mutations without re-provisioning infrastructure.

        Delegates to base_changes.revert_all() for the actual revert, then clears the preset.
        """
        if self.is_running:
            yield "[ERROR] Another operation is already in progress\n"
            return

        self._current_operation = ResetOperation.QUICK
        self._current_status = ResetStatus.RUNNING
        self._operation_start_time = datetime.utcnow()

        from services.mutation_tracker import mutation_tracker
        from services import base_changes

        total = mutation_tracker.count()

        yield "=" * 60 + "\n"
        yield f"[INFO] Quick Revert — reverting {total} tracked mutations\n"
        yield "=" * 60 + "\n\n"

        errors = await base_changes.revert_all()

        # No preset (we've reverted everything)
        mutation_tracker.set_preset(None)

        yield "\n" + "=" * 60 + "\n"
        if errors:
            self._current_status = ResetStatus.COMPLETED
            yield f"[SUCCESS] Quick revert completed with {len(errors)} warning(s)\n"
            for err in errors:
                yield f"  [WARNING] {err}\n"
        else:
            self._current_status = ResetStatus.COMPLETED
            yield "[SUCCESS] Quick revert completed successfully\n"
        yield "=" * 60 + "\n"

    async def cancel_operation(self) -> bool:
        """Cancel the current running operation."""
        if not self.is_running:
            return False
        self._current_status = ResetStatus.FAILED
        logger.info("Reset operation cancelled")
        return True


# Global reset service instance
reset_service = ResetService()
