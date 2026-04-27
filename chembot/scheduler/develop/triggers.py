import uuid
from typing import Collection
import abc
import time
from datetime import datetime, timedelta


class Trigger(abc.ABC):
    """
    Abstract base class for all trigger types.

    Purpose
    -------
    - Define a common interface for condition-based triggering logic.
    - Allow scheduling, events, or workflows to wait on arbitrary conditions.
    - Support time-based, signal-based, or combined trigger mechanisms.

    Design Notes
    ------------
    - Triggers are passive objects: they do not block or execute actions.
    - External code polls the `triggered` property to test readiness.
    """

    @property
    @abc.abstractmethod
    def triggered(self) -> bool:
        """
        Return True if the trigger condition has been satisfied.
        """
        ...


class TriggerNow(Trigger):
    """
    Trigger that fires immediately.

    Purpose
    -------
    - Represent unconditional execution.
    - Useful as a default trigger or for testing.
    """

    def __init__(self):
        pass

    def __str__(self):
        return type(self).__name__

    def triggered(self) -> bool:
        """Always returns True."""
        return True


class TriggerTimeRelative(Trigger):
    """
    Trigger that fires after a relative time interval elapses.

    Also known as an interval trigger.
    """

    def __init__(self, trigger_time: timedelta):
        """
        Parameters
        ----------
        trigger_time : timedelta
            Amount of time after start until the trigger fires.
        """
        self.trigger_time = trigger_time
        self._start_time = None  # expected to be set externally

    def __str__(self):
        return f"{type(self).__name__} | trigger_time: {self.trigger_time}"

    def triggered(self) -> bool:
        """
        Returns True once current time exceeds start time + trigger_time.
        """
        return self.trigger_time + self._start_time > time.time()


class TriggerTimeAbsolute(Trigger):
    """
    Trigger that fires at or after a specific absolute point in time.
    """

    def __init__(self, trigger_time: datetime):
        """
        Parameters
        ----------
        trigger_time : datetime
            Absolute timestamp when trigger should fire.
        """
        self.trigger_time = trigger_time

    def __str__(self):
        return f"{type(self).__name__} | trigger_time: {self.trigger_time}"

    def triggered(self) -> bool:
        """Return True if current time is past trigger_time."""
        return self.trigger_time > datetime.now()


class TriggerSignal(Trigger):
    """
    Trigger that fires when an external signal is received.

    Purpose
    -------
    - Allow decoupled components to synchronize via a shared signal value.
    - Useful for manual triggers, callbacks, or inter-process coordination.
    """

    def __init__(self, signal: int | float | str = None):
        """
        Parameters
        ----------
        signal : int | float | str, optional
            Signal value required to trigger. Generated automatically if omitted.
        """
        self.signal = signal if signal is not None else uuid.uuid4().int
        self._signaled = False

    def __str__(self):
        return f"{type(self).__name__} | signal: {self.signal}"

    def triggered(self) -> bool:
        """Return True if the signal has been received."""
        return self._signaled

    def set_signal(self, signal: int | float | str) -> bool:
        """
        Set the trigger if the provided signal matches.

        Returns
        -------
        bool
            True if the signal matched and trigger fired.
        """
        if signal == self.signal:
            self._signaled = True
            return True
        return False


class TriggerCombine(Trigger, abc.ABC):
    """
    Abstract base class for composite triggers.

    Purpose
    -------
    - Combine multiple triggers into higher-level logic constructs.
    """

    ...


class TriggerOr(TriggerCombine):
    """
    Trigger that fires when ANY of its child triggers fires.
    """

    def __init__(self, triggers: Collection[Trigger]):
        self.triggers = triggers

    def __str__(self):
        return f"{type(self).__name__} | number_triggers: {len(self)}"

    def __len__(self):
        return len(self.triggers)

    def triggered(self) -> bool:
        """Return True if at least one trigger is active."""
        return any(trigger.triggered for trigger in self.triggers)


class TriggerAnd(TriggerCombine):
    """
    Trigger that fires when ALL of its child triggers fire.
    """

    def __init__(self, triggers: Collection[Trigger]):
        self.triggers = triggers

    def __str__(self):
        return f"{type(self).__name__} | number_triggers: {len(self)}"

    def __len__(self):
        return len(self.triggers)

    def triggered(self) -> bool:
        """Return True only if all triggers are active."""
        return all(trigger.triggered for trigger in self.triggers)