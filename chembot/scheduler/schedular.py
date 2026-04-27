from __future__ import annotations

import time
from datetime import timedelta, datetime
import logging

from chembot.configuration import config
from chembot.scheduler.event import Event
from chembot.scheduler.job import Job
from chembot.scheduler.schedule import Schedule


logger = logging.getLogger(config.root_logger_name + ".schedular")


class Schedular:
    """
    Central scheduling engine for Chembot jobs and events.

    Purpose
    -------
    - Maintain an ordered queue of Jobs.
    - Convert Jobs into a concrete Schedule of Events per Resource.
    - Determine which Event should execute next based on time.
    - Track running, completed, and queued jobs.

    Design Notes
    ------------
    - The Schedular does not execute events itself.
    - It only decides *what* should run next and *when*.
    - Actual execution is handled by the MasterController.
    """

    # Delay applied before starting a new job if the queue is empty
    delay = timedelta(seconds=2)

    def __init__(self, timer: callable = None):
        """
        Initialize the scheduler.

        Parameters
        ----------
        timer : callable, optional
            Time source for scheduling (defaults to time.monotonic).
        """
        # Optional injectable timer (useful for testing)
        self.timer = timer if timer is not None else time.monotonic

        # Concrete event schedule (resources → ordered events)
        self.schedule = Schedule()

        # Job currently running
        self._job_running: Job | None = None

        # Completed jobs history
        self._jobs_completed: list[Job] = []

        # Jobs waiting to be started
        self._jobs_in_queue: list[Job] = []

    def __str__(self):
        """Human-readable summary of scheduler state."""
        return (
            "Schedular:"
            f"\n\tRunning: {self._job_running}"
            f"\n\tCompleted: {self._jobs_completed}"
            f"\n\tIn queue (# jobs: {len(self._jobs_in_queue)}):  {self._jobs_in_queue}"
        )

    # -------------------------
    # Job state accessors
    # -------------------------

    @property
    def jobs_completed(self) -> list[Job]:
        """Return list of completed jobs."""
        return self._jobs_completed

    @property
    def job_running(self) -> Job | None:
        """Return the currently running job."""
        return self._job_running

    @property
    def jobs_in_queue(self) -> list[Job]:
        """Return list of jobs waiting to be executed."""
        return self._jobs_in_queue

    @property
    def time_end(self) -> datetime | None:
        """
        Return the end time of the last scheduled job or running job.
        """
        if self._jobs_in_queue:
            return self._jobs_in_queue[-1].time_end
        if self._job_running:
            return self._job_running.time_end
        return None

    # -------------------------
    # Event dispatch logic
    # -------------------------

    def get_event_to_run(self) -> Event | None:
        """
        Determine the next Event that is ready to execute.

        Behavior
        --------
        - Iterate over all resources in the Schedule.
        - For each resource, check its next Event.
        - If the Event's scheduled start time has passed, return it.
        - Update job tracking state accordingly.

        Returns
        -------
        Event | None
            The next Event to execute, or None if nothing is ready.
        """
        now = datetime.now()

        for resource in self.schedule.resources:
            if resource.next_event is None:
                continue

            if now > resource.next_event.time_start_with_delay:
                next_event = resource.next_event

                # Update job execution tracking
                self._update_job_lists(next_event.root)

                # Advance resource event pointer
                resource.next_event_index += 1

                return next_event

    def _update_job_lists(self, job):
        """
        Update internal job tracking when a new job starts executing.
        """
        if job is not self._job_running:
            if self._job_running is not None:
                self._jobs_completed.append(self._job_running)
            self._job_running = self._jobs_in_queue.pop(0)

    # -------------------------
    # Job management
    # -------------------------

    def add_job(self, job: Job):
        """
        Add a new job to the scheduler.

        Behavior
        --------
        - Assign a feasible start time.
        - Append job to the execution queue.
        - Expand job into the Schedule (resources + events).
        """
        # Determine earliest feasible start time
        time_start = self.get_possible_start_time_for_schedule()
        job.time_start = time_start

        # Queue job
        self._jobs_in_queue.append(job)

        # Expand job into schedule
        self.schedule.add_job(job)

    def get_possible_start_time_for_schedule(self) -> datetime:
        """
        Compute the earliest start time for a new job.

        Notes
        -----
        - If the schedule is empty or finished, start after `delay`.
        - This is a simple heuristic and not fully robust.
        """
        end_time = self.time_end

        if end_time is None or end_time < datetime.now():
            end_time = datetime.now()

        return end_time + self.delay

    def clear_all_jobs(self):
        """
        Remove all jobs and reset the scheduler state.

        Behavior
        --------
        - Clears running and queued jobs.
        - Resets schedule completely.
        """
        self._job_running = None
        self._jobs_in_queue = []
        self.schedule = Schedule()
