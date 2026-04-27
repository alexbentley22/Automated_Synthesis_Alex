from __future__ import annotations

import uuid
from typing import Iterator

from chembot.scheduler.event import Event
from chembot.scheduler.job import Job
from chembot.scheduler.resource import Resource


def loop_through_jobs(schedule: Schedule, obj: Job | Event):
    """
    Depth-first traversal of a Job graph to extract Events.

    Purpose
    -------
    - Recursively walk through nested Jobs and Events.
    - Register every Event with the appropriate Resource in the Schedule.
    - Flatten hierarchical job structures into resource-specific timelines.

    Parameters
    ----------
    schedule : Schedule
        Schedule object being populated.
    obj : Job | Event
        Current node in the job graph.
    """
    if isinstance(obj, Event):
        # Leaf node: assign event to its resource
        schedule.add_event(obj.resource, obj)

    elif isinstance(obj, Job):
        # Recursive descent into child events/jobs
        for obj_ in obj.events:
            loop_through_jobs(schedule, obj_)

    else:
        raise ValueError("Not valid event.")


class Schedule:
    """
    Concrete execution schedule derived from one or more Jobs.

    Purpose
    -------
    - Represent a flattened, executable view of all Events grouped by Resource.
    - Maintain the association between Jobs and Resources.
    - Provide the structure used by the Schedular to determine execution order.

    Design Notes
    ------------
    - A Schedule is rebuilt whenever jobs are added or cleared.
    - Resources inside the Schedule maintain their own ordered event lists.
    """

    def __init__(self, id_: int = None):
        """
        Parameters
        ----------
        id_ : int, optional
            Unique identifier for the schedule (defaults to a random UUID).
        """
        self.id_ = id_ if id_ is not None else uuid.uuid4().int

        # Resources participating in this schedule
        self._resources: list[Resource] = []

        # Cached resource labels (names)
        self._resources_labels = []

        # Jobs contributing events to this schedule
        self._jobs = []

    def __str__(self):
        """Compact summary of schedule contents."""
        return f"jobs: {len(self._jobs)} | resources: {len(self._resources)}"

    def __iter__(self):
        """Allow iteration over resources."""
        return iter(self._resources)

    # -------------------------
    # Accessors
    # -------------------------

    @property
    def resources(self) -> list[Resource]:
        """Return all resources in the schedule."""
        return self._resources

    @property
    def jobs(self) -> list[Job]:
        """Return all jobs associated with the schedule."""
        return self._jobs

    @property
    def number_of_resources(self) -> int:
        """Return the number of distinct resources."""
        return len(self._resources)

    @property
    def resources_labels(self) -> list[str]:
        """Return all resource names."""
        return [resource.name for resource in self.resources]

    def get_resources(self, name: str) -> Resource:
        """
        Retrieve a Resource object by name.

        Raises
        ------
        ValueError
            If resource name is not found.
        """
        index = self.resources_labels.index(name)
        return self._resources[index]

    # -------------------------
    # Resource management
    # -------------------------

    def add_resource(self, resource: Resource | Iterator[Resource]):
        """
        Add one or more resources to the schedule.
        """
        if isinstance(resource, Resource):
            resource = [resource]

        self._resources += resource

    # -------------------------
    # Job management
    # -------------------------

    def get_job(self, job: str) -> Job:
        """
        Retrieve a job by name or identifier.

        Notes
        -----
        - Not implemented.
        - Provided as an extension point.
        """
        pass

    def add_job(self, job: Job):
        """
        Add a job and expand it into the schedule.

        Behavior
        --------
        - Append the job to internal job list.
        - Traverse the job hierarchy and register all Events.
        """
        self._jobs.append(job)
        loop_through_jobs(self, job)

    # -------------------------
    # Event management
    # -------------------------

    def get_event(self, event: str | int) -> Event:
        """
        Retrieve an Event from the schedule.

        Notes
        -----
        - Not implemented.
        - Event lookup may be by name or ID.
        """
        raise NotImplementedError

    def add_event(self, resource: str | Resource, event: Event):
        """
        Assign an Event to a Resource inside the schedule.

        Behavior
        --------
        - Automatically creates the Resource if it does not exist.
        - Delegates ordering of events to Resource.add_event().
        """
        if isinstance(resource, str):
            if resource in self.resources_labels:
                resource = self.get_resources(resource)
            else:
                resource = Resource(resource)
                self.add_resource(resource)

        elif isinstance(resource, Resource):
            if resource not in self.resources:
                self.add_resource(resource)

        else:
            raise ValueError("Invalid 'resource' value")

        resource.add_event(event)

    # -------------------------
    # Factory constructors
    # -------------------------

    @classmethod
    def from_job(cls, job: Job) -> Schedule:
        """
        Construct a Schedule from a single Job.

        This is commonly used for validation or inspection.
        """
        schedule = cls(job.id_)
        schedule.add_job(job)
        return schedule
