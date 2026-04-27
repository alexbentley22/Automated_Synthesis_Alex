from __future__ import annotations

import uuid
from typing import Protocol, Callable
from datetime import datetime, timedelta


class Parent(Protocol):
    """
    Structural typing protocol that describes the minimal interface required
    by an Event's parent (typically a Job or a Job-like scheduler object).

    Purpose
    -------
    - Avoid tight coupling between Event and a concrete Job implementation.
    - Allow Event to access scheduling metadata (start times, job IDs)
      without knowing the full Job class.

    Required Attributes
    -------------------
    time_start : datetime
        Nominal start time of the parent.
    name : str
        Human-readable name of the job.
    root : Parent
        Root job object (used when events are nested).
    id_job : int
        Unique identifier for the job.

    Required Methods
    ----------------
    _get_time_start(self, obj) -> datetime
        Compute the scheduled start time of a specific Event.
    """
    time_start: datetime
    name: str
    root: Parent
    id_job: int

    def _get_time_start(self, obj) -> datetime:
        ...


class Event:
    """
    Representation of a single scheduled action (event) within a job.

    Purpose
    -------
    - Describe *what* to do (callable/action),
      *where* to do it (resource),
      and *when* to do it (schedule/duration).
    - Serve as the atomic unit the scheduler executes and visualizes.

    Design Notes
    ------------
    - Events are immutable with respect to identity (id_), but track runtime
      status via `completed`.
    - Timing is derived from the parent job/schedule rather than stored locally.
    """

    def __init__(
        self,
        resource: str,
        callable_: str | Callable,
        duration: timedelta,
        *,
        delay: timedelta = None,
        kwargs: dict[str, object] = None,
        priority: int = 0,
        name: str = None,
        parent: Parent = None,
    ):
        """
        Parameters
        ----------
        resource : str
            Name of the resource/device that executes the action.
        callable_ : str | Callable
            Action name or callable to invoke on the resource.
        duration : timedelta
            Execution duration of the action (excluding delay).
        delay : timedelta, optional
            Optional delay added before execution starts.
        kwargs : dict[str, object], optional
            Arguments passed to the action.
        priority : int, optional
            Relative priority used by the scheduler when resolving conflicts.
        name : str, optional
            Human-readable name for the event.
        parent : Parent, optional
            Owning job or job-like object.
        """
        # Normalize callable to string name
        if isinstance(callable_, Callable):
            callable_ = callable_.__name__

        # Default event name if not provided
        if name is None:
            name = f"{resource}.{callable_}"

        self.name = name
        self.resource = resource
        self.callable_ = callable_
        self._duration = duration
        self.priority = priority
        self.kwargs = kwargs
        self.delay = delay
        self.parent = parent

        # Unique event identifier
        self.id_ = uuid.uuid4().int

        # Runtime status flag
        self.completed = False

    def __str__(self):
        """
        Human-readable representation, useful for logs and debugging.
        """
        text = f"{self.resource}.{self.callable_}("
        if self.kwargs is not None:
            text += ",".join(f"{k}: {v}" for k, v in self.kwargs.items())
        text += ")"
        return text + f" | {self.duration}"

    def __repr__(self):
        return self.__str__()

    @property
    def id_job(self) -> int:
        """
        Shortcut to the owning job's identifier.
        """
        return self.parent.id_job

    @property
    def time_start(self) -> datetime:
        """
        Scheduled start time of this event (excluding any delay).
        """
        return self.parent._get_time_start(self)

    @property
    def time_start_with_delay(self) -> datetime:
        """
        Scheduled start time including the optional delay.
        """
        time_ = self.time_start
        if self.delay is not None:
            time_ += self.delay
        return time_

    @property
    def time_end(self) -> datetime:
        """
        Scheduled end time of the event (start + delay + duration).
        """
        return self.time_start_with_delay + self._duration

    @property
    def root(self) -> Parent:
        """
        Return the root job object for nested scheduling structures.
        """
        return self.parent.root

    @property
    def duration(self) -> timedelta:
        """
        Duration of the event execution.

        Notes
        -----
        - Represents execution time; delay is handled separately.
        """
        return self._duration

    def hover_text(self) -> str:
        """
        Rich-text summary for UI visualization (e.g., timeline tooltips).
        """
        return (
            f"duration: {self.duration}<br>"
            f"job: {self.parent.root.name}<br>"
            f"action: {self.callable_}"
        )