from chembot.scheduler.event import Event


class Resource:
    """
    Representation of a schedulable execution resource.

    Purpose
    -------
    - Model a concrete system resource (e.g., pump, valve, heater, robot arm)
      that can execute Events over time.
    - Maintain a time-ordered list of Events assigned to this resource.
    - Provide helpers to determine which Event should execute next.

    Design Notes
    ------------
    - A Resource does NOT execute events directly.
    - It acts as a scheduling container used by the scheduler.
    - Execution responsibility lives elsewhere (e.g., MasterController).
    """

    def __init__(self, name: str):
        """
        Parameters
        ----------
        name : str
            Logical name of the resource (used for routing and identification).
        """
        self.name = name

        # Ordered list of events scheduled on this resource
        self._events: list[Event] = []

        # Index of the next event to be executed
        self.next_event_index: int = 0

    def __str__(self):
        """Human-readable summary."""
        return f"{self.name} | # events: {len(self._events)}"

    def __repr__(self):
        return self.__str__()

    # -------------------------
    # Event accessors
    # -------------------------

    @property
    def next_event(self) -> Event | None:
        """
        Return the next scheduled event for this resource.

        Returns
        -------
        Event | None
            The next Event to execute, or None if all events are completed.
        """
        if self.next_event_index == len(self._events):
            return None
        return self._events[self.next_event_index]

    @property
    def events(self) -> list[Event]:
        """
        Return all scheduled events for this resource.
        """
        return self._events

    # -------------------------
    # Event scheduling
    # -------------------------

    def add_event(self, event: Event):
        """
        Insert an Event into the resource's schedule in chronological order.

        Behavior
        --------
        - Events are ordered by their computed `time_start`.
        - Insertion begins from the end of the list for efficiency,
          since most events are appended in increasing time order.
        - Ensures the internal event list remains sorted.
        """
        # Insert events in temporal order:
        # start from the end and move backward until correct position is found.
        for i, event_ in enumerate(reversed(self._events)):
            if event.time_start > event_.time_start:
                if i == 0:
                    self._events.append(event)
                else:
                    self._events.insert(-i, event)
                return

        # If no later start time found, insert at the beginning
        self._events.insert(0, event)

    def validate_event(self, event: Event):
        """
        Validate whether an Event can be scheduled on this resource.

        Intended Purpose
        ----------------
        - Check for temporal conflicts (overlapping events).
        - Enforce resource-specific constraints.
        - Prevent invalid scheduling configurations.

        Notes
        -----
        - Currently unimplemented.
        - Expected to raise errors on invalid scheduling conditions.
        """
        pass
