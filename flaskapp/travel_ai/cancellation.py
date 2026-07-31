"""Cooperative workflow cancellation primitives."""


class PlanningCancelled(RuntimeError):
    """Raised between agent stages after a user cancels a planning job."""
