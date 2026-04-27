from __future__ import annotations

import abc
from datetime import timedelta, datetime
from typing import Collection
import uuid

from chembot.scheduler.event import Event


class Job(abc.ABC):
    """
    Abstract base class representing a schedulable Job.

    Purpose
    -------
    - Group one or more Events (or nested Jobs) into a higher-level unit of work.
    - Provide a common interface for sequential and concurrent scheduling models.
    - Track identity, completion state, timing, and hierarchical structure.

    Design Notes
    ------------
    - Jobs can be nested (a Job can contain Jobs).
    - Timing is derived hierarchically from parents.
    - Subclasses implement how event start times are computed.
    """

    def __init__(
        self,
        events: Collection[Event | Job, ...] = None,
        delay: timedelta = None,
        name: str = None,
        parent: Job = None,
        time_start: datetime = None,
    ):
        # Unique identifier for this job instance
        self.id_ = uuid.uuid4().int

        # Optional human-readable name
        self.name = name

        # Optional delay applied before job starts
        self.delay = delay

        # Track event IDs to prevent duplicates
        self._ids: set[int] = set()

        # Ordered list of child events/jobs
        self._events: list[Event | Job] = []

        # Add initial events, if provided
        if events is not None:
            self.add_event(events)

        # Hierarchical linkage
        self.parent = parent

        # Runtime completion flag
        self.completed = False

        # Explicit start time (only valid for root jobs)
        self._time_start = time_start

    def __str__(self):
        """
        Human-readable summary of the job.
        """
        text = type(self).__name__
        if self.name is not None:
            text += "|" + self.name
        text += f"(# events: {len(self)})"
        text += "completed" if self.completed else "not completed"
        return text

    def __repr__(self):
        return self.__str__()

    def __len__(self) -> int:
        """
        Return total number of Events contained in this Job,
        including nested Jobs.
        """
        count = 0
        for ev in self.events:
            if isinstance(ev, Job):
                count += len(ev)
            else:
                count += 1
        return count

    @property
    def id_job(self) -> int:
        """
        Return the root job identifier.

        Notes
        -----
        - Nested jobs share the same `id_job` as their root.
        """
        if self.parent is not None:
            return self.parent.id_job
        return self.id_

    @property
    def time_start_with_delay(self) -> datetime:
        """
        Job start time including job-level delay.
        """
        time_ = self.time_start
        if self.delay is not None:
            time_ += self.delay
        return time_

    @property
    def time_start(self) -> datetime | None:
        """
        Job start time excluding delay.

        Notes
        -----
        - Root jobs use `_time_start`.
        - Nested jobs defer to parent scheduling logic.
        """
        if self.parent is not None:
            return self.parent._get_time_start(self)
        if self._time_start is not None:
            return self._time_start

    @time_start.setter
    def time_start(self, time_start: datetime):
        """
        Set start time for root jobs only.
        """
        if self.parent is not None:
            raise ValueError("Start time can not be set if there is a parent")
        self._time_start = time_start

    @property
    @abc.abstractmethod
    def duration(self) -> timedelta:
        """
        Total duration of the job (including child events and delays).

        Must be implemented by subclasses.
        """
        ...

    @property
    def time_end(self) -> datetime | None:
        """
        Compute end time of the job.
        """
        if self.time_start is None:
            return None
        return self.time_start + self.duration

    @property
    def events(self) -> list[Event | Job, ...]:
        """
        Ordered list of child Events or Jobs.
        """
        return self._events

    @property
    def root(self) -> Job:
        """
        Return the root Job in the hierarchy.
        """
        if self.parent is None:
            return self
        return self.parent.root

    def add_event(self, event: Collection[Event | Job, ...] | Event | Job):
        """
        Add one or more events/jobs to this job.

        Notes
        -----
        - Ensures no duplicate event IDs enter the job.
        - Automatically assigns parent relationship.
        """
        if isinstance(event, (Job, Event)):
            event = [event]

        self._id_check(event)
        self._events += event

        for event_ in event:
            event_.parent = self

    def _id_check(self, events: Collection[Event | Job, ...]):
        """
        Ensure no duplicate Event IDs are added to the job tree.
        """
        for event in events:
            if event.id_ in self._ids:
                raise ValueError(
                    "Duplicate event not allowed. Events must be re-made from scratch to be performed twice.\n"
                    f"Duplicate event:{event.name} (id: {event.id_})"
                )

            self._ids.add(event.id_)

            # Recursively check nested jobs
            if isinstance(event, Job):
                if len(self._ids.intersection(event._ids)) != 0:
                    raise ValueError(
                        "Duplicate event not allowed. Events must be re-made from scratch to be performed twice.\n"
                        f"Duplicate event:{event.name} (id: {event.id_})"
                    )
                self._ids = self._ids.union(event._ids)

    @abc.abstractmethod
    def _get_time_start(self, obj) -> datetime:
        """
        Compute the start time of a child Event or Job.

        Implemented by subclasses to define scheduling semantics.
        """
        ...


class JobSequence(Job):
    """
    Job that executes events sequentially.

    Each event starts when the previous one ends.
    """

    @property
    def duration(self) -> timedelta:
        """
        Total duration is the sum of event durations and delays.
        """
        sum_ = timedelta(0)
        for event_ in self.events:
            if event_.delay:
                event_duration = event_.duration + event_.delay
            else:
                event_duration = event_.duration
            sum_ += event_duration
        return sum_

    def _get_time_start(self, obj) -> datetime:
        """
        Start time is the end time of the previous event.
        """
        index = self.events.index(obj)
        if index == 0:
            return self.time_start_with_delay
        return self.events[index - 1].time_end


class JobConcurrent(Job):
    """
    Job that executes events concurrently.

    All events start at the same time.
    """

    @property
    def duration(self) -> timedelta:
        """
        Total duration is the maximum of all event durations (including delays).
        """
        max_ = timedelta(0)
        for event_ in self.events:
            if event_.delay:
                event_duration = event_.duration + event_.delay
            else:
                event_duration = event_.duration

            if max_ < event_duration:
                max_ = event_duration
        return max_

    def _get_time_start(self, obj) -> datetime:
        """
        All events start at the same time.
        """
        return self.time_start_with_delay