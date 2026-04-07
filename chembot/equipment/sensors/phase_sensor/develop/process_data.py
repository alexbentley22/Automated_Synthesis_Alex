import numpy as np

import plotly.graph_objs as go
from plotly.subplots import make_subplots
from unitpy import Unit, Quantity

from chembot.utils.algorithms.change_detection import CUSUM


class Slug:
    """
    Data model representing a single slug (liquid segment) traveling past two sensors.

    Purpose
    -------
    - Stores sensor event times: rising/falling edges for sensor 1 and 2.
    - Computes slug metrics using physical dimensions:
        * velocity  (mm/s)
        * length    (mm)
        * volume    (uL)
    - All dimensional math is unit‑safe (Quantity).

    Assumptions
    -----------
    sensor_spacer : distance between the two phase sensors.
    tube_diameter : inner tubing diameter at measurement point.
    """

    sensor_spacer = 0.95 * Unit.cm
    tube_diameter = 0.0762 * Unit.cm
    __slots__ = ("time_start_1", "time_end_1", "time_start_2", "time_end_2", "_length", "_velocity")

    def __init__(
            self,
            time_start_1: float | int,
            time_end_1: float | int = None,
            time_start_2: float | int = None,
            time_end_2: float | int = None,
            velocity: Quantity | None = None
    ):
        # Primary timestamps
        self.time_start_1 = time_start_1
        self.time_end_1 = time_end_1
        self.time_start_2 = time_start_2
        self.time_end_2 = time_end_2

        # Cached geometric quantities (computed lazily)
        self._length = None
        self._velocity = velocity

    def __str__(self):
        """
        Provide readable summary:
        - If volume known → include velocity, length, and volume
        - If partial detection → include start time + velocity if available
        - If incomplete → show minimal info
        """
        if self.volume is not None:
            text = f"vel:{self.velocity:3.2f}, len: {self.length:3.2f}, vol:{self.volume:3.2f}"
        elif self.time_span:
            text = f"start: {self.time_start_1:3.2f}"
            if self.velocity is not None:
                text += f", velocity: {self.velocity:3.2f})"
        else:
            text = f"start: {self.time_start_1:3.2f}, No end detected"

        return text

    @property
    def time_span(self) -> Quantity | None:
        """
        Duration of slug interaction with sensor 1 (seconds).

        Returns None unless time_end_1 is valid.
        """
        if self.time_end_1 is None:
            return None
        t = self.time_end_1 - self.time_start_1
        if isinstance(t, Quantity):
            return t
        return t * Unit.s

    @property
    def time_offset(self):
        """
        Midpoint drift between sensors (average of start and end differences).

        Returns Quantity[s] or None.
        """
        if self.is_complete:
            t = (self.time_start_2 - self.time_start_1 + self.time_end_2 - self.time_end_1) / 2
            if isinstance(t, Quantity):
                return t
            return t * Unit.s
        return None

    @property
    def is_complete(self) -> bool:
        """Slug is considered 'complete' when both sensors have start + end events."""
        return not (
            self.time_end_1 is None or
            self.time_end_2 is None or
            self.time_start_2 is None
        )

    @property
    def velocity(self) -> Quantity | None:
        """
        Compute slug velocity based on sensor spacing and time_span.

        Units: mm/s
        """
        if self._velocity is None:
            if not self.is_complete:
                return None
            self._velocity = self.sensor_spacer / self.time_span

        return self._velocity.to("mm/s")

    @property
    def length(self) -> Quantity | None:
        """
        Slug length = velocity × time_span.

        Units: mm
        """
        if self._length is None:
            if self.velocity is None or self.time_span is None:
                return None
            self._length = self.velocity * self.time_span

        return self._length.to('mm')

    @property
    def volume(self) -> Quantity | None:
        """
        Slug volume treated as a cylinder: length * π * (ID/2)^2.

        Units: uL
        """
        if self.length is None:
            return None
        return (self.length * np.pi * (self.tube_diameter / 2) ** 2).to("uL")


def main2(path):
    """
    Process a CSV recording, detect slugs with CUSUM, and visualize:

    Steps
    -----
    1. Load time-signal CSV.
    2. Normalize time to t=0 at start.
    3. Use CUSUM to detect upward/downward edges.
    4. Construct Slug objects as edges appear.
    5. Plot raw data + CUSUM traces + slug markers using Plotly.
    """
    velocity = 0.3 * Unit("ml/min") / (np.pi * (Slug.tube_diameter / 2) ** 2)

    data = np.genfromtxt(path, delimiter=",")
    data[:, 0] = data[:, 0] - data[0, 0]   # zero time offset

    algorithm = CUSUM()
    slugs = []
    up = np.zeros(data.shape[0])
    down = np.zeros(data.shape[0])

    for i in range(data.shape[0]):
        new_data_point = data[i, 1]
        event = algorithm.add_data(new_data_point)
        up[i] = algorithm._up_sum
        down[i] = algorithm._low_sum

        if event is CUSUM.States.up:
            slugs.append(Slug(time_start_1=data[i, 0], velocity=velocity))
        if event is CUSUM.States.down and slugs:
            slugs[-1].time_end_1 = data[i, 0]

    # Print slug summary
    for i, slug in enumerate(slugs):
        print(i, slug)
    print("avg. volume: ", sum([slug.volume for slug in slugs[:-1]]) / len(slugs))

    # ---- Plot using Plotly ----
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    # Raw signals
    fig.add_trace(go.Scatter(x=data[:, 0], y=data[:, 1], mode="lines"))
    fig.add_trace(go.Scatter(x=data[:, 0], y=data[:, 2], mode="lines"))

    # CUSUM curves
    fig.add_trace(
        go.Scatter(x=data[:, 0], y=up, mode="lines", legendgroup="CUSUM"),
        secondary_y=False
    )
    fig.add_trace(
        go.Scatter(x=data[:, 0], y=down, mode="lines", legendgroup="CUSUM"),
        secondary_y=False
    )

    # Mark detected slugs
    for slug in slugs:
        add_slug(fig, slug)

    fig.write_html("temp.html", auto_open=True)


def add_slug(fig, slug: Slug):
    """
    Add a stylized rectangle/slanted marker representing a slug event window.

    The visual consists of 6 points drawn to produce:
        start: a vertical mark
        end:   a second vertical mark
    """
    x = [
        slug.time_start_1,
        slug.time_start_1,
        slug.time_start_1,
        slug.time_end_1,
        slug.time_end_1,
        slug.time_end_1,
    ]
    y = [-1, 1, 0, 0, 1, -1]

    fig.add_trace(
        go.Scatter(x=x, y=y, mode="lines", legendgroup="process"),
        secondary_y=True
    )


def main(path):
    """
    Simplified debugging mode:
    - Computes CUSUM state transitions and plots only raw signal + CUSUM curves.
    - Does not compute slug geometry.
    """
    data = np.genfromtxt(path, delimiter=",")
    data[:, 0] = data[:, 0] - data[0, 0]

    algorithm = CUSUM()
    state = np.zeros(data.shape[0], dtype=np.int8)
    up = np.zeros(data.shape[0])
    down = np.zeros(data.shape[0])

    for i in range(data.shape[0]):
        new_data_point = data[i, 1]
        event = algorithm.add_data(new_data_point)
        up[i] = algorithm._up_sum
        down[i] = algorithm._low_sum
        if event:
            state[i] = event.value

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Scatter(x=data[:, 0], y=data[:, 1], mode="lines"))

    fig.add_trace(
        go.Scatter(x=data[:, 0], y=up, mode="lines", legendgroup="process"),
        secondary_y=True
    )
    fig.add_trace(
        go.Scatter(x=data[:, 0], y=down, mode="lines", legendgroup="process"),
        secondary_y=True
    )

    fig.write_html("temp.html", auto_open=True)


if __name__ == "__main__":
    path_ = (
        r"C:\Users\nicep\Desktop\Reseach_Post\python\chembot\chembot\equipment\sensors\phase_sensor\develop"
        r"\data_0.csv"
    )
    main2(path_)