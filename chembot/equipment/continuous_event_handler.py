import abc
import typing
from typing import Callable, Sequence
import time
import logging

import numpy as np

from chembot.configuration import config
from chembot.rabbitmq.messages import RabbitMessage, RabbitMessageAction
from chembot.utils.buffers.buffers import BufferSavable
from chembot.utils.buffers.buffer_ring import BufferRingTimeSavable

logger = logging.getLogger(config.root_logger_name + ".continuous_event_handler")


class ParentInterfaceContinuousEventHandler(typing.Protocol):
    """
    A minimal protocol describing what the 'parent' object must provide for a
    ContinuousEventHandler to operate.

    Any equipment/device class that wishes to use a ContinuousEventHandler
    should conform to this protocol.

    Attributes
    ----------
    name : str
        Human-readable identifier of the parent (used for file names/logging).

    Methods
    -------
    _execute_action(message: RabbitMessage, func_name: str, kwargs: dict | None)
        Execute the named callable (by string) on the parent using the provided kwargs,
        possibly dispatching through RabbitMQ and/or the parent’s internal logic.
    """
    name: str

    def _execute_action(self, message: RabbitMessage, func_name: str, kwargs: dict | None):
        ...


class ContinuousEventHandler(abc.ABC):
    """
    Base class for time-driven, periodic or scheduled actions against a parent device.

    The typical usage pattern:
      1) An equipment/device sets `handler.message` (a RabbitMessageAction context) if needed.
      2) The scheduler/device loop calls `handler.poll(parent)` frequently.
      3) When the current time reaches/exceeds `next_time`, the handler:
           - computes kwargs via `_get_kwargs()`
           - calls `parent._execute_action(...)`
           - increments `event_counter`
           - schedules the next trigger time via `_set_next_time()`

    Subclasses implement:
      - `_get_kwargs()`     → what arguments to pass at each event
      - `_set_next_time()`  → when the next poll should fire

    Attributes
    ----------
    callable_ : str
        Name of the callable to invoke on the parent (string form).
    message : RabbitMessageAction | None
        Optional Rabbit context to associate with the action.
    _start_time : float | None
        Absolute start time for the schedule (epoch seconds).
    _next_time : float | None
        Next absolute time when the handler should trigger (epoch seconds).
    event_counter : int
        How many events have been executed so far.
    """

    def __init__(self, callable_: str | Callable):
        # Normalize callable to a string function name
        self.callable_ = callable_ if isinstance(callable_, str) else callable_.__name__
        self.message: RabbitMessageAction | None = None

        self._start_time = None
        self._next_time = 0
        self.event_counter: int = 0

    def __str__(self):
        return f"{type(self).__name__}.{self.callable_}"

    def __repr__(self):
        return self.__str__()

    @property
    def next_time(self) -> float | None:
        """
        Return the next absolute trigger time (epoch seconds), or None if not scheduled.
        """
        return self._next_time

    @property
    def start_time(self) -> float | None:
        """
        Return the absolute start time (epoch seconds), or None if not set.
        """
        return self._start_time

    @start_time.setter
    def start_time(self, start_time: float):
        """
        Set the start time and compute the initial next_time.

        Raises
        ------
        ValueError
            If `start_time` is earlier than the current system time.
        """
        if start_time < time.time():
            raise ValueError("Start time can't be earlier than current time.")

        self._start_time = start_time
        self._set_next_time()

    def poll(self, parent: ParentInterfaceContinuousEventHandler):
        """
        Check whether it is time to execute the next event; if so, fire it.

        This should be called frequently by the parent’s main loop. If `time.time()`
        has reached/exceeded `self._next_time`, then:
          - schedule the subsequent next_time (`_set_next_time`)
          - increment `event_counter`
          - call the parent's action executor with computed kwargs
          - return the parent's execution result (if any)

        If it's not time yet, returns immediately (None).

        Parameters
        ----------
        parent : ParentInterfaceContinuousEventHandler
            The equipment/device instance that will execute the action.

        Returns
        -------
        Any | None
            Whatever the parent action returns, or None if not executed this cycle.
        """
        # Not yet time to trigger
        if self._next_time > time.time():
            return

        # Schedule the next trigger before executing the current one
        self._set_next_time()

        # Execute call
        self.event_counter += 1
        return parent._execute_action(self.message, self.callable_, self._get_kwargs())

    @abc.abstractmethod
    def _get_kwargs(self) -> dict:
        """
        Compute the keyword arguments to pass to the parent's action for this event.
        Must be implemented by subclasses.
        """
        ...

    @abc.abstractmethod
    def _set_next_time(self):
        """
        Compute and set `self._next_time` for the next trigger.
        Must be implemented by subclasses.
        """
        ...

    def stop(self):
        """
        Optional hook for subclasses to perform cleanup (e.g., flush buffers).
        """
        pass


class ContinuousEventHandlerRepeatingNoEnd(ContinuousEventHandler):
    """
    Simple repeating handler with a fixed delay between events and no end condition.

    Parameters
    ----------
    callable_ : str | Callable
        Name or reference to the callable (normalized to str).
    kwargs : dict[str, ...] | None
        Static keyword arguments passed on every event.
    delay_between_measurements : float | int
        Delay between successive events (seconds).
    """

    def __init__(self,
                 callable_: str | Callable,
                 kwargs: dict[str, ...] = None,
                 delay_between_measurements: float | int = 0,  # in seconds
                 ):
        super().__init__(callable_)

        self.kwargs = kwargs
        self.delay_between_measurements = delay_between_measurements

    def __str__(self):
        if self.kwargs is not None:
            text = f"({''.join([str(k)+'='+str(v) for k, v in self.kwargs.items()])})"
        else:
            text = "()"
        return super().__str__() + text

    def _get_kwargs(self) -> dict:
        """
        Return the static kwargs assigned to this handler (may be None).
        """
        return self.kwargs

    def _set_next_time(self):
        """
        Schedule the next trigger at current_time + delay_between_measurements.
        """
        self._next_time = time.time() + self.delay_between_measurements


class ContinuousEventHandlerRepeating(ContinuousEventHandlerRepeatingNoEnd):
    """
    Repeating handler that stops after a fixed number of events.

    Parameters
    ----------
    callable_ : str | Callable
    kwargs : dict[str, ...]
        Static kwargs to pass on each event.
    max_repeats : int
        Number of events after which the parent should clear the profile.
    delay_between_measurements : float | int
        Delay between events (seconds).
    """

    def __init__(self,
                 callable_: str | Callable,
                 kwargs: dict[str, ...],
                 max_repeats: int,
                 delay_between_measurements: float | int = 0,  # in seconds
                 ):
        super().__init__(callable_, kwargs, delay_between_measurements)
        self.max_repeats = max_repeats

    def poll(self, parent: ParentInterfaceContinuousEventHandler):
        """
        Trigger the next event if due, and clear the parent's profile after `max_repeats`.
        """
        super().poll(parent)
        if self.max_repeats is not None and self.max_repeats == self.event_counter:
            # Convention in parent: setting profile to None stops scheduling
            parent.profile = None


class ContinuousEventHandlerProfile(ContinuousEventHandler):
    """
    Profile-driven handler: drive a sequence of values according to a time schedule.

    This handler schedules a series of events at absolute offsets from "now".
    At each event:
      - It selects the appropriate set of kwargs from `kwargs_values`.
      - Invokes the parent's callable with those kwargs.

    Parameters
    ----------
    callable_ : str | Callable
        Name or reference to the callable (normalized to str).
    kwargs_names : Sequence[str]
        Names of the keyword arguments to pass at each event. If length==1, we accept a
        1-D sequence for `kwargs_values`. Otherwise, expect a 2-D structure (N x M).
    kwargs_values : Sequence
        Values corresponding to each event. Either:
          - length N, if `kwargs_names` has length 1, or
          - shape (N, M), pairing with `kwargs_names`.
    time_of_measurements : np.ndarray[float | int]
        Array of time offsets (seconds) from "now" for each event, length N.

    Raises
    ------
    ValueError
        If `len(kwargs_values) != len(time_of_measurements)` for the 1-D case.
    """

    def __init__(self,
                 callable_: str | Callable,
                 kwargs_names: Sequence[str],
                 kwargs_values: Sequence,
                 time_of_measurements: np.ndarray[float | int],
                 ):
        super().__init__(callable_)
        self.kwargs_names = kwargs_names

        if len(kwargs_values) != len(time_of_measurements):
            raise ValueError("len(kwargs_values) must equal len(time_delta_values).\n"
                             f"\tlen(kwargs_values): {len(kwargs_values)}"
                             f"\tlen(kwargs_values): {len(time_of_measurements)}"
                             )
        self.kwargs_values = kwargs_values
        self.time_of_measurements = time_of_measurements
        self._times = None   # absolute times computed on first _set_next_time
        self._done = False   # set True when near the end of the profile

    @property
    def done(self) -> bool:
        """
        Whether the profile has completed its scheduled events.
        """
        return self._done

    def poll(self, parent: ParentInterfaceContinuousEventHandler):
        """
        If not done, perform the base poll behavior; otherwise, do nothing.
        """
        if not self._done:
            return ContinuousEventHandler.poll(self, parent)

    def _get_kwargs(self) -> dict:
        """
        Build the kwargs for the current `event_counter` index.

        Returns
        -------
        dict
            If `len(kwargs_names) == 1`, returns `{name: value}` (scalar).
            Otherwise returns `{name_i: value_i}` for the row at `event_counter`.
        """
        if len(self.kwargs_names) == 1:
            return {self.kwargs_names[0]: self.kwargs_values[self.event_counter]}
        return {k: v for k, v in zip(self.kwargs_names, self.kwargs_values[self.event_counter, :])}

    def _set_next_time(self):
        """
        Compute `self._times` (absolute schedule) on first call and set `_next_time`.

        Behavior
        --------
        - On the first trigger, convert relative `time_of_measurements` to absolute times
          by adding `time.time()` (now).
        - If we are at the penultimate index (len-2), mark `_done=True` and set `_next_time=None`
          after scheduling the current event time. (This ensures the last event is scheduled
          and then the handler will not schedule further.)
        """
        if self.event_counter == 0:
            logger.debug(f"start time: {time.time()}")
            self._times = self.time_of_measurements + time.time()

        if self.event_counter == len(self.time_of_measurements) - 2:
            self._done = True
            self._next_time = None

        self._next_time = self._times[self.event_counter]


# class ContinuousEventHandlerRepeatingConditional(ContinuousEventHandlerRepeating):
#     """
#     Example (commented out): Repeating handler with a stopping condition.
#     Could evaluate a callable `condition()` and stop when it returns True.
#     """
#     def __init__(self,
#                  callable_: str | Callable,
#                  kwargs: dict[str, ...],
#                  max_repeats: int,
#                  condition: Callable,
#                  delay_between_measurements: float | int = 0,  # in seconds
#                  ):
#         super().__init__(callable_, kwargs, delay_between_measurements)
#         self.max_repeats = max_repeats
#
#     def poll(self, parent: ParentInterfaceContinuousEventHandler):
#         super().poll(parent)
#         if self.max_repeats is not None and self.max_repeats == self.event_counter:
#             parent.profile = None


class ContinuousEventHandlerRepeatingNoEndSaving(ContinuousEventHandlerRepeatingNoEnd):
    """
    Repeating handler that optionally saves each result into a file-backed buffer.

    This is useful for periodic measurements (e.g., sensors) that you want to
    log to disk while streaming.

    Parameters
    ----------
    callable_ : str | Callable
        Name or reference to the callable (normalized to str).
    kwargs : dict[str, ...] | None
        Static kwargs to pass on every event (may be None).
    buffer_type : type | BufferRingTimeSavable | None
        If provided, will be used to instantiate a savable buffer (e.g., CSV) on first result.
    delay_between_measurements : float | int
        Delay between events (seconds).

    Attributes
    ----------
    _buffer_type : type | BufferRingTimeSavable | None
        The buffer class to use for saving, if not None.
    buffer : BufferSavable | None
        The instantiated buffer (created lazily, when the first result arrives).
    """

    def __init__(self,
                 callable_: str | Callable,
                 kwargs: dict[str, ...] = None,
                 buffer_type: type | BufferRingTimeSavable = None,
                 delay_between_measurements: float | int = 0,  # in seconds
                 ):
        super().__init__(callable_, kwargs, delay_between_measurements)
        self._buffer_type = buffer_type
        self.buffer: BufferSavable | None = None

    def poll(self, parent: ParentInterfaceContinuousEventHandler):
        """
        Trigger a measurement when due; optionally append the result to a savable buffer.

        Lazy buffer creation
        --------------------
        To avoid sending large arrays over RabbitMQ (or constructing buffers too early),
        the buffer is instantiated only when the first `result` is received, using:
            buffer = buffer_type(config.data_directory / (parent.name + ".csv"))

        Returns
        -------
        Any | None
            Parent execution result (also saved), or None if not time to trigger.
        """
        result = super().poll(parent)
        if result is None:
            return

        if self._buffer_type is not None:
            if self.buffer is None:
                # Delay creating the buffer until the handler is attached to a specific equipment
                # (and we have a concrete parent name/path).
                self.buffer = self._buffer_type(config.data_directory / (parent.name + ".csv"))
            self.buffer.add_data(result)

        return result

    def stop(self):
        """
        Flush and persist any buffered data when the handler is stopped.
        """
        logger.debug(f"{type(self).__name__}.stop() called.  Buffer: {type(self.buffer).__name__}")
        if self.buffer is not None:
            self.buffer.save_all()
        logger.debug(f"{type(self).__name__}.stop() called.  after")