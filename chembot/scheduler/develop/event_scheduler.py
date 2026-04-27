"""
Designed using elements from Python's sched.scheduler.

This module provides a general-purpose, thread-safe event scheduler that
can execute actions at specified times with priorities, including support
for recurring events.
"""

import enum
import heapq
import sys
import time
import threading

_sentinel = object()


def run_threaded(job_func: callable, args: dict | None, kwargs: dict | None):
    """
    Execute a job function in a separate thread.

    Purpose
    -------
    - Allow non-blocking execution of scheduled actions.
    - Prevent long-running actions from stalling the scheduler thread.
    """
    job_thread = threading.Thread(target=job_func, args=args, kwargs=kwargs)
    job_thread.start()


class Event:
    """
    Lightweight container representing a scheduled event.

    Purpose
    -------
    - Store timing, priority, callable, and arguments for scheduled execution.
    - Support comparison operations so events can be ordered in a heap.
    """

    __slots__ = ("id_", "time_", "priority", "action", "args", "kwargs")

    def __init__(self, id_: int, time_, priority: int, action: callable, args: tuple, kwargs: dict):
        """
        Parameters
        ----------
        id_ : int
            Identifier for this event (0 for non-recurring).
        time_ : float
            Absolute execution time (same scale as timefunc).
        priority : int
            Lower numbers execute first when times are equal.
        action : callable
            Function invoked when the event executes.
        args : tuple
            Positional arguments for the action.
        kwargs : dict
            Keyword arguments for the action.
        """
        self.id_ = id_
        self.time_ = time_
        self.priority = priority
        self.action = action
        self.args = args
        self.kwargs = kwargs

    def __repr__(self):
        return str((self.id_, self.time_, self.priority, self.action, self.args, self.kwargs))

    # Comparison operators enable heapq ordering
    def __eq__(self, obj):
        return (self.time_, self.priority) == (obj.time_, obj.priority)

    def __lt__(self, obj):
        return (self.time_, self.priority) < (obj.time_, obj.priority)

    def __le__(self, obj):
        return (self.time_, self.priority) <= (obj.time_, obj.priority)

    def __gt__(self, obj):
        return (self.time_, self.priority) > (obj.time_, obj.priority)

    def __ge__(self, obj):
        return (self.time_, self.priority) >= (obj.time_, obj.priority)

    def __iter__(self):
        """
        Enable tuple-like unpacking of Event attributes.
        """
        return iter((self.id_, self.time_, self.priority, self.action, self.args, self.kwargs))


class SchedulerStatus(enum.Enum):
    """
    Lifecycle states of the EventScheduler.
    """
    RUNNING = 0
    STOPPING = 1
    STOPPED = 2


class EventScheduler:
    """
    Thread-based event scheduler with priority and recurring support.

    Purpose
    -------
    - Accept events scheduled for absolute or relative times.
    - Execute events when their scheduled times are reached.
    - Support recurring events with fixed intervals.
    - Provide blocking or non-blocking execution models.
    """

    def __init__(self,
                 timefunc=time.time,
                 timer_class=threading.Timer,
                 blocking: bool = False):
        """
        Parameters
        ----------
        timefunc : callable
            Time source used for scheduling.
        timer_class : type
            Timer class compatible with timefunc.
        blocking : bool
            If True, actions run in scheduler thread.
            If False, actions run in separate threads.
        """
        self._queue = []
        self._lock = threading.RLock()
        self.timefunc = timefunc
        self._scheduler_status = SchedulerStatus.STOPPED
        self._event_thread = threading.Thread(target=self._run)

        # Condition variable used to coordinate scheduling thread
        self._cv = threading.Condition(self._lock)

        # Timer used when next event is in the future
        self._timer_class = timer_class
        self._timer = None

        # Track recurring events by id
        self._recurring_events = {}

        # Counter for generating unique recurring event IDs
        self._id_counter = 0

        self.blocking = blocking

    def empty(self) -> bool:
        """Return True if no events are scheduled."""
        with self._lock:
            return not self._queue

    def _notify(self):
        """Notify scheduler thread of changes."""
        with self._cv:
            self._cv.notify()

    def enterabs(self, time_: float, action: callable,
                 args: tuple = (), kwargs=_sentinel, priority: int = 5) -> Event:
        """
        Schedule an event at an absolute time.
        """
        if priority >= sys.maxsize or priority < 0:
            raise ValueError("Priority must be >= 0 and < sys.maxsize")

        if kwargs is _sentinel:
            kwargs = {}

        event = Event(0, time_, priority, action, args, kwargs)

        with self._lock:
            if self._scheduler_status != SchedulerStatus.RUNNING:
                return None

            heapq.heappush(self._queue, event)

            # Notify only if this event is now earliest
            if event == self._queue[0]:
                self._notify()

        return event

    def enter(self, delay: float, action: callable,
              args: tuple = (), kwargs=_sentinel, priority: int = 5) -> Event:
        """
        Schedule an event after a time delay relative to now.
        """
        if priority >= sys.maxsize or priority < 0:
            raise ValueError("Priority must be >= 0 and < sys.maxsize")

        time_ = self.timefunc() + delay
        return self.enterabs(time_, action, args, kwargs, priority)

    def enter_recurring(self, interval, priority, action,
                        arguments=(), kwargs=_sentinel) -> int:
        """
        Schedule a recurring event that executes repeatedly at a fixed interval.
        """
        if priority >= sys.maxsize or priority < 0:
            raise ValueError("Priority must be >= 0 and < sys.maxsize")

        if kwargs is _sentinel:
            kwargs = {}

        with self._lock:
            if self._scheduler_status != SchedulerStatus.RUNNING:
                return None

            self._id_counter += 1
            time_ = self.timefunc() + interval

            event = Event(self._id_counter, time_, priority, action, arguments, kwargs)
            self._recurring_events[self._id_counter] = (event, interval)

            heapq.heappush(self._queue, event)

            if event == self._queue[0]:
                self._notify()

            return self._id_counter

    def _reschedule_recurring(self, *args):
        """
        Internal helper to schedule the next occurrence of a recurring event.
        """
        time_, priority, action, argument, kwargs, event_id = args

        if event_id in self._recurring_events and self._scheduler_status == SchedulerStatus.RUNNING:
            interval = self._recurring_events[event_id][1]
            event = Event(event_id, interval + time_, priority, action, argument, kwargs)
            self._recurring_events[event_id] = (event, interval)
            heapq.heappush(self._queue, event)

    def cancel(self, event: Event) -> int:
        """
        Cancel a scheduled (non-recurring) event.
        """
        with self._lock:
            if self._scheduler_status != SchedulerStatus.RUNNING:
                return -1
            try:
                if self._queue and self._queue[0] == event:
                    self._notify()
                self._queue.remove(event)
                heapq.heapify(self._queue)
            except ValueError:
                pass
        return 0

    def cancel_recurring(self, event_id) -> int:
        """
        Cancel a recurring event by its ID.
        """
        with self._lock:
            if self._scheduler_status != SchedulerStatus.RUNNING:
                return -1
            if event_id not in self._recurring_events:
                return 0

            event = self._recurring_events[event_id][0]
            del self._recurring_events[event_id]

            if self._queue and self._queue[0] == event:
                self._notify()

            self._queue.remove(event)
            heapq.heapify(self._queue)
            return 0

    def cancel_all(self) -> int:
        """
        Cancel all scheduled and recurring events.
        """
        with self._lock:
            if self._scheduler_status != SchedulerStatus.RUNNING:
                return -1

            if self._queue:
                self._queue.clear()

            if self._timer:
                self._timer.cancel()
                self._timer = None

            self._recurring_events.clear()

        return 0

    def _run(self):
        """
        Main scheduler loop.

        Executes events in order of time and priority until stopped.
        """
        cv = self._cv
        q = self._queue
        timefunc = self.timefunc
        pop = heapq.heappop
        timer = None

        while True:
            with cv:
                if not q or timer:
                    cv.wait()

                if timer:
                    timer.cancel()
                    timer = None

                if not q:
                    continue

                event_id, time_, priority, action, args, kwargs = q[0]

                # Sentinel event signals shutdown
                if priority == sys.maxsize:
                    pop(q)
                    self._notify()
                    break

                now = timefunc()

                if time_ > now:
                    timer = self._timer_class(time_ - now, self._notify)
                    timer.start()
                    self._notify()
                    continue
                else:
                    pop(q)

                if event_id:
                    self._reschedule_recurring(time_, priority, action,
                                               args, kwargs, event_id)

                # Execute event
                if self.blocking:
                    action(*args, **kwargs)
                else:
                    run_threaded(action, args, kwargs)

                self._notify()

    @property
    def queue(self) -> list:
        """
        Return a snapshot of all upcoming events in execution order.
        """
        with self._lock:
            events = self._queue[:]
            self._notify()

        return list(map(heapq.heappop, [events] * len(events)))

    def start(self) -> int:
        """
        Start the scheduler thread.
        """
        with self._lock:
            if self._scheduler_status != SchedulerStatus.STOPPED:
                return -1
            self._event_thread.start()
            self._scheduler_status = SchedulerStatus.RUNNING
        return 0

    def stop(self, hard_stop: bool = False) -> int:
        """
        Stop the scheduler thread.

        Parameters
        ----------
        hard_stop : bool
            If True, discards pending events immediately.
        """
        with self._lock:
            if self._scheduler_status != SchedulerStatus.RUNNING:
                return -1

            if hard_stop:
                self.cancel_all()

            self._scheduler_status = SchedulerStatus.STOPPING

            last_event = Event(0, self.timefunc(), 0, None, (), {})
            if self._queue:
                last_event = max(self._queue)

            # Sentinel event ensures clean termination
            event = Event(0, last_event.time_, sys.maxsize, None, (), {})
            heapq.heappush(self._queue, event)
            self._notify()

        time.sleep(0)
        self._event_thread.join()

        with self._lock:
            self._scheduler_status = SchedulerStatus.STOPPED

        return 0