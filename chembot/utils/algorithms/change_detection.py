import enum

import numpy as np

from chembot.utils.buffers.buffer_ring import BufferRing


class CUSUM:
    """
    Cumulative Sum (CUSUM) change detection algorithm.

    Purpose
    -------
    - Detect small but persistent shifts in the mean of a signal.
    - Provide early detection of drift, faults, or regime changes.
    - Operate incrementally on streaming data.

    References
    ----------
    - https://en.wikipedia.org/wiki/CUSUM
    - https://www.mathworks.com/help/signal/ref/cusum.html

    Design Notes
    ------------
    - This implementation supports three states: init, up, down.
    - Uses a sliding window (BufferRing) to estimate mean and variance.
    - Designed for real-time, online use.
    """

    class States(enum.Enum):
        """
        Discrete states representing detected signal behavior.
        """
        init = 0
        down = 1
        up = 2

    def __init__(
        self,
        c_limit: float | int = 3,
        n: int = 31,
        sigma: int | float = 4,
    ):
        """
        Parameters
        ----------
        c_limit : float | int
            Control limit multiplier.
            Defines how many standard deviations constitute a detected event.
        n : int
            Number of samples used to compute mean and standard deviation.
        sigma : float | int
            Minimum detectable mean shift, expressed in standard deviations.
        """
        self.c_limit = c_limit
        self.n = n
        self.sigma = sigma

        # Detection state tracking
        self._state = self.States.init
        self._prior_state = self.States.init

        # CUSUM accumulators
        self._up_sum = 0
        self._low_sum = 0

        # Running statistics
        self._mean = 0
        self._standard_deviation = 0

        # Buffer for recent samples
        self._data = None
        self._count = 0

    @property
    def state(self) -> States:
        """
        Current detected CUSUM state.
        """
        return self._state

    def _init_data(self, data: np.ndarray) -> None:
        """
        Initialization phase for the CUSUM algorithm.

        Behavior
        --------
        - Fills the initial data buffer.
        - Estimates mean and standard deviation once enough samples exist.
        - Initializes CUSUM accumulators.
        """
        try:
            # Fast path if buffer already exists
            self._data.add_data(data)
        except AttributeError:
            # Lazy initialization of circular buffer
            self._data = BufferRing(length=self.n)
            self._data.add_data(data)

        if self._count > 3:
            # Estimate background statistics
            self._mean = np.mean(self._data.buffer[:self._count])
            self._standard_deviation = np.std(self._data.buffer[:self._count])

            # Update CUSUM accumulators
            self._up_sum = np.max(
                (
                    0,
                    self._up_sum
                    + data
                    - self._mean
                    - 0.5 * self.sigma * self._standard_deviation,
                )
            )
            self._low_sum = np.min(
                (
                    0,
                    self._low_sum
                    + data
                    - self._mean
                    + 0.5 * self.sigma * self._standard_deviation,
                )
            )

        self._count += 1

    def add_data(self, data: np.ndarray) -> States | None:
        """
        Add a new data point and update CUSUM state.

        Purpose
        -------
        - Incrementally process streaming data.
        - Detect upward or downward mean shifts.
        - Emit events only on state transitions.

        Parameters
        ----------
        data : np.ndarray
            New data sample.

        Returns
        -------
        States | None
            - Returns a new state when a transition is detected.
            - Returns None when no event occurs.
        """
        # Initialization phase
        if self._count < self.n:
            self._init_data(data)
            return None

        # Update rolling buffer and statistics
        self._data.add_data(data)
        self._mean = np.mean(self._data.buffer)
        self._standard_deviation = np.std(self._data.buffer)

        # Update CUSUM accumulators
        self._up_sum = np.max(
            (
                0,
                self._up_sum
                + data
                - self._mean
                - 0.5 * self.sigma * self._standard_deviation,
            )
        )
        self._low_sum = np.min(
            (
                0,
                self._low_sum
                + data
                - self._mean
                + 0.5 * self.sigma * self._standard_deviation,
            )
        )

        # Detect upward or downward shift
        if self._up_sum > self.c_limit * self._standard_deviation:
            self._state = self.States.up

        if self._low_sum < -self.c_limit * self._standard_deviation:
            self._state = self.States.down

        # Emit event only on state change
        if self._state != self._prior_state:
            self._prior_state = self._state
            return self._state

        return None  # No event detected