from __future__ import annotations

import typing
from typing import Sequence, Iterator
from datetime import datetime, timedelta


class TimeBlockInterface(typing.Protocol):
    """
    Structural typing interface for time blocks used in a Gantt chart.

    Purpose
    -------
    - Define the minimal attributes required for visualizing a time interval.
    - Allow different time block implementations as long as they conform
      to the same attribute contract.

    Required Attributes
    -------------------
    time_start : datetime
        Start time of the block.
    time_end : datetime
        End time of the block (None for instantaneous/ongoing events).
    name : str
        Identifier used for labeling or debugging.
    hover_text : str
        Text shown when hovering over the block in the UI.
    """
    time_start: datetime
    time_end: datetime
    name: str
    hover_text: str


class TimeBlock:
    """
    Concrete implementation of a time interval used in Gantt charts.

    Purpose
    -------
    - Represent a single contiguous interval on the time axis.
    - Carry optional labeling and hover-text metadata.

    Notes
    -----
    - Supports sorting by start time.
    - Can represent open-ended blocks (time_end is None).
    """

    counter = 0

    def __init__(
        self,
        time_start: datetime,
        time_end: datetime = None,
        name: str = None,
        hover_text: str = None,
    ):
        self.time_start = time_start
        self.time_end = time_end

        # Automatically generate a name if not provided
        self.name = name if name is not None else f"time_block_{self.counter}"

        self.hover_text = hover_text
        TimeBlock.counter += 1

    def __lt__(self, other: TimeBlock):
        """Enable sorting time blocks by start time."""
        return self.time_start < other.time_start

    def __eq__(self, other):
        return self.time_start == other.time_start


class Row:
    """
    Single row in a Gantt chart (e.g., a resource or job lane).

    Purpose
    -------
    - Group related time blocks together under a single label.
    - Maintain blocks in chronological order.
    """

    def __init__(self, name: str, time_blocks: list[TimeBlockInterface] = None):
        self.name = name

        # Time blocks sorted by time_start
        self.time_blocks = time_blocks if time_blocks is not None else []

    def __len__(self) -> int:
        """Return number of time blocks in the row."""
        return len(self.time_blocks)

    @property
    def time_block_names(self) -> list[str]:
        """Return names of all time blocks in this row."""
        return [time_block.name for time_block in self.time_blocks]

    def add_time_block(self, time_block: TimeBlockInterface):
        """
        Add a time block and keep blocks ordered by time.
        """
        self.time_blocks.append(time_block)
        self.time_blocks.sort()


class GanttChart:
    """
    Data model representing a full Gantt chart.

    Purpose
    -------
    - Organize rows and their time blocks.
    - Track global time bounds for plotting.
    - Provide a stable, inspection‑friendly structure for visualization layers.
    """

    def __init__(self, rows: list[Row] = None, current_time: datetime = None):
        self._rows = []

        # Initialize rows if provided
        if rows is not None:
            self.add_row(rows)

        # Optional timestamp used for "now" overlays
        self.current_time = current_time

        # Cached computed properties
        self._time_min = None
        self._time_max = None
        self._row_labels = []

        # Cache invalidation flag
        self._up_to_date = False

    def __iter__(self):
        """Allow iteration directly over rows."""
        return iter(self._rows)

    @property
    def rows(self) -> list[Row]:
        return self._rows

    @property
    def number_of_rows(self) -> int:
        """Return number of rows in the chart."""
        return len(self._rows)

    @property
    def row_labels(self) -> list[str]:
        """
        Names of all rows.

        Automatically recomputed if data has changed.
        """
        if not self._up_to_date:
            self._update()
        return self._row_labels

    @property
    def time_min(self) -> datetime | None:
        """
        Earliest start time across all rows.
        """
        if not self._up_to_date:
            self._update()
        return self._time_min

    @property
    def time_max(self) -> datetime | None:
        """
        Latest end time across all rows.
        """
        if not self._up_to_date:
            self._update()
        return self._time_max

    @property
    def time_range(self) -> timedelta:
        """
        Total span of the Gantt chart.
        """
        return self.time_max - self.time_min

    def _update(self):
        """
        Recompute cached min/max times and row labels.
        """
        self._time_min, self._time_max = get_min_max_time(self.rows)
        self._row_labels = [row.name for row in self.rows]
        self._up_to_date = True

    def add_row(self, row: Row | Iterator[Row]):
        """
        Add one or more rows to the chart.
        """
        self._up_to_date = False
        if isinstance(row, Row):
            row = [row]
        self._rows += list(row)

    def get_row(self, row: str) -> Row:
        """
        Retrieve a row by name.
        """
        index = self.row_labels.index(row)
        return self._rows[index]

    def add_time_block(self, row: str, time_block: TimeBlockInterface):
        """
        Add a time block to a named row.

        Automatically creates the row if it does not exist.
        """
        if row not in self.row_labels:
            self.add_row(Row(row, []))

        row_obj = self.get_row(row)
        row_obj.add_time_block(time_block)

        self._up_to_date = False

    def delete_row(self, row: Row | Iterator[Row] | int):
        """
        Remove one or more rows from the chart.
        """
        self._up_to_date = False

        if isinstance(row, int):
            del self._rows[row]
        elif isinstance(row, Row):
            self._rows.remove(row)
        elif isinstance(row, Iterator):
            for row_ in row:
                self._rows.remove(row_)
        else:
            raise ValueError("Invalid argument provided.")


def get_min_max_time(data: Sequence[Row]) -> tuple[datetime | None, datetime | None]:
    """
    Compute minimum start time and maximum end time across all rows.
    """
    if len(data) == 0 or len(data[0].time_blocks) == 0:
        return None, None

    min_time = data[0].time_blocks[0].time_start
    max_time = min_time

    for row in data:
        for time_block in row.time_blocks:
            if time_block.time_start < min_time:
                min_time = time_block.time_start
            if time_block.time_end is not None and time_block.time_end > max_time:
                max_time = time_block.time_end

    return min_time, max_time


def get_time_delta_label(time_delta: timedelta) -> str:
    """
    Convert a time delta into a human‑readable label.
    """
    if time_delta >= timedelta(days=1):
        return f"{time_delta.days} d"
    if time_delta >= timedelta(hours=1):
        return f"{int(time_delta.seconds / 3600)} h"
    if time_delta >= timedelta(minutes=1):
        return f"{int(time_delta.seconds / 60)} min"
    if time_delta >= timedelta(seconds=1):
        return f"{time_delta.seconds} s"

    return f"{time_delta.microseconds} µs"