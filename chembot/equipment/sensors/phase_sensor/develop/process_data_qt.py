import sys
import time

import numpy as np

from scipy import sparse
from scipy.sparse.linalg import spsolve

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import QApplication, QMainWindow
from pyqtgraph.dockarea import *
import pyqtgraph as pg

from typing import Annotated, Literal, TypeVar
import numpy.typing as npt

DType = TypeVar("DType", bound=np.generic)

# Type aliases (as annotated shapes/semantics)
array_int16_nx2 = Annotated[npt.NDArray[np.int16], Literal["n", 2]]
array_bool_n = Annotated[bool, Literal["n"]]   # NOTE: kept as in original; likely intended as an array of bool


class MyWindow(QMainWindow):
    """
    Simple PyQt6 / pyqtgraph window for live plotting of phase-sensor data.

    Purpose
    -------
    - Create a DockArea with a single plot widget.
    - Periodically update plots/labels via a QTimer driving `update_all`/`update`.

    Notes
    -----
    - Several identifiers used here (e.g., `self.w2`, `update_all`, `xdata`, `ydata1`,
      `self.l1`, `self.l2`, `self.fps_time`) are *not defined* in the original
      source. This annotated version preserves the code verbatim and documents
      these likely/intentional placeholders.

    - To make this runnable, you’d typically:
        * define `self.w2` or change `self.w2.plot(...)` to `self.w1.plot(...)`,
        * implement/rename `update_all` → `update` or vice versa,
        * initialize `self.fps_time`, `self.l1`, `self.l2`,
        * provide live data arrays (`xdata`, `ydata1`).
    """

    def __init__(self, app):
        super(MyWindow, self).__init__()
        self.app = app
        self.initUI()

    def initUI(self):
        """
        Build the DockArea, the main plot, and start a timer driving updates.
        """
        # Generate window
        self.area = DockArea()
        self.setCentralWidget(self.area)
        self.resize(1200, 600)
        self.setWindowTitle('Phase sensor')

        # Generate docks
        self.d1 = Dock("Dock1", size=(1200, 600))
        self.area.addDock(self.d1)

        # Dock 1
        self.d1.hideTitleBar()
        self.w1 = pg.PlotWidget(title="phase sensor")
        self.d1.addWidget(self.w1)
        self.w1.setLabel('left', 'Distance', units='cm')
        self.w1.setLabel('bottom', 'Time', units='sec')
        self.plot_points = 100
        self.w1_xdata = np.zeros([self.plot_points])
        self.w1_ydata = np.zeros([self.plot_points])
        pen_w1_1 = pg.mkPen(color=(0, 0, 255), width=5)
        # NOTE: self.w2 is not defined; kept as-is per original
        self.w1_plot = self.w2.plot(self.w1_xdata, self.w1_ydata, pen=pen_w1_1)

        # Update everything
        self.counter = 0
        self.w2_timer = pg.QtCore.QTimer()
        # NOTE: update_all is not defined; original code connects to it
        self.w2_timer.timeout.connect(self.update_all)
        self.w2_timer.start(0)

    def update(self):
        """
        Periodic update routine:
        - refresh plots,
        - update labels every 10th call,
        - process Qt events,
        - increment an internal counter.
        """
        self.update_plots()
        if self.counter % 10 == 0:   # run every 10 loop
            self.update_labels()
        self.app.processEvents()
        self.counter += 1

    def update_plots(self):
        """
        Plot update hook.

        Notes
        -----
        - Uses `xdata`, `ydata1` globals which are not defined in the original file.
          Preserved as-is to avoid changing logic.
        """
        global data
        self.w1_plot.setData(xdata, ydata1)

    def update_labels(self):
        """
        Label update hook (FPS, iteration counter).

        Notes
        -----
        - Refers to `self.fps_time`, `self.l1`, `self.l2` which are not initialized.
          Preserved per original source.
        """
        now = time.time()
        dt = time.time() - self.fps_time
        self.fps_time = now
        self.fps = 10 / dt
        self.l1.setValue(self.fps)
        self.l2.setValue(self.counter)


def exponential_filter(data: np.ndarray, a: float = 0.9):
    """
    Exponential smoothing filter along rows (time), columns 1..end.

    Parameters
    ----------
    data : np.ndarray
        2D array with time in col 0 and values in cols 1..
    a : float
        Smoothing factor (0..1). Larger → heavier smoothing.

    Returns
    -------
    np.ndarray
        Smoothed copy of the input data.
    """
    data = np.copy(data)
    for row in range(1, len(data)):
        data[row, 1:] = a * data[row - 1, 1:] + (1 - a) * data[row, 1:]
    return data


def adaptive_polynomial_baseline(
        x: np.ndarray,
        y: np.ndarray,
        remove_amount: float = 0.4,
        deg: int = 0,
        num_iter: int = 5) \
        -> tuple[np.ndarray, np.ndarray]:
    """ Adaptive polynomial baseline correction

    Algorithm
    ---------
    1) Fit polynomial of degree `deg` to (x_mask, y_mask).
    2) Remove a fraction of points farthest from the fit.
    3) Refit to the reduced (masked) dataset.
    4) Increase the removal fraction and repeat for `num_iter` iterations.
    5) Return x and baseline-corrected y (y - y_baseline).

    Parameters
    ----------
    x : np.ndarray
        x data
    y : np.ndarray
        y data
    remove_amount : float
        (0..1) total fraction removed cumulatively across iterations
    deg : int
        polynomial degree (0..10 typical)
    num_iter : int
        number of iterations (>= 2 recommended)

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        (x, y_corrected), where y_corrected = y - y_baseline

    Raises
    ------
    ValueError
        If `num_iter` < 1
    """
    if num_iter < 1:
        raise ValueError("'num_iter' must be larger than 1.")

    x_mask = x
    y_mask = y
    number_of_points_to_remove_each_iteration = int(len(x) * remove_amount / num_iter)
    for i in range(num_iter):
        # perform fit
        params = np.polyfit(x_mask, y_mask, deg)
        func_baseline = np.poly1d(params)
        y_baseline = func_baseline(x)

        if i != num_iter:  # skip on last iteration (as in original)
            # get values furthest from the baseline
            number_of_points_to_remove = number_of_points_to_remove_each_iteration * (i + 1)
            index_of_points_to_remove = np.argsort(np.abs(y - y_baseline))[-number_of_points_to_remove:]
            y_mask = np.delete(y, index_of_points_to_remove)
            x_mask = np.delete(x, index_of_points_to_remove)

    y = y - y_baseline

    return x, y


def baseline_als(x: np.ndarray, y: np.ndarray, lam: float = 1_000, p: float = 0.01, niter=10):
    """
    Asymmetric Least Squares Smoothing baseline (Eilers & Boelens).

    Parameters
    ----------
    x : np.ndarray
        x values (passed through)
    y : np.ndarray
        y values to smooth/baseline
    lam : float
        smoothness parameter (lambda)
    p : float
        asymmetry parameter (0..1), smaller favors baseline below data
    niter : int
        number of iterations

    Returns
    -------
    (x, z) : tuple[np.ndarray, np.ndarray]
        x and the estimated baseline z.
    """
    L = len(y)
    D = sparse.diags([1, -2, 1], [0, -1, -2], shape=(L, L - 2))
    w = np.ones(L)
    for i in range(niter):
        W = sparse.spdiags(w, 0, L, L)
        Z = W + lam * D.dot(D.transpose())
        z = spsolve(Z, w * y)
        w = p * (y > z) + (1 - p) * (y < z)
    return x, z


def canny_edge_detector_1d(data, low_threshold, high_threshold):
    """
    Very rough 1D "Canny-like" edge estimate (blur → gradient → NMS → threshold).

    Parameters
    ----------
    data : array-like
        1D signal
    low_threshold : float
        (unused in this simplified version; preserved)
    high_threshold : float
        threshold applied after non-maximum suppression

    Returns
    -------
    np.ndarray
        Edge strength after NMS and thresholding (same length as input).
    """
    # Apply Gaussian blur to reduce noise (optional for 1D data)
    blurred_data = np.convolve(data, np.ones(5) / 5, mode='same')

    # Calculate the gradient magnitude using central differences
    gradient = np.gradient(blurred_data)

    # Calculate the magnitude of the gradient
    gradient_magnitude = np.abs(gradient)

    # Non-maximum suppression
    edges = np.zeros_like(gradient_magnitude)
    for i in range(1, len(edges) - 1):
        if gradient_magnitude[i] > gradient_magnitude[i - 1] and gradient_magnitude[i] > gradient_magnitude[i + 1]:
            edges[i] = gradient_magnitude[i]

    # Apply thresholding to find strong edges
    edges = (edges > high_threshold) * edges

    return edges


def convert_to_binary(array, threshold):
    """
    Convert a numeric array into a 0/1 mask using a scalar threshold.

    Parameters
    ----------
    array : array-like
        Signal values
    threshold : float
        Threshold value

    Returns
    -------
    np.ndarray
        Binary array of the same shape (0/1).
    """
    binary_array = np.where(array > threshold, 1, 0)
    return binary_array


class Slug:
    """
    Simple slug model for the 1D processing path (units in seconds, centimeters).

    Attributes
    ----------
    time_start_1, time_end_1, time_start_2, time_end_2 : float | int | None
        Event times on sensor 1 and 2 (rising/falling).
    sensor_spacer : float
        Distance between sensors (cm).
    """
    sensor_spacer = 0.95  # cm
    __slots__ = ("time_start_1", "time_end_1", "time_start_2", "time_end_2", "_length", "_velocity")

    def __init__(self,
                 time_start_1: float | int,
                 time_end_1: float | int = None,
                 time_start_2: float | int = None,
                 time_end_2: float | int = None
                 ):
        self.time_start_1 = time_start_1
        self.time_end_1 = time_end_1
        self.time_start_2 = time_start_2
        self.time_end_2 = time_end_2
        self._length = None
        self._velocity = None

    def __str__(self):
        if not self.is_complete:
            text = f"slug_start: ({self.time_start_1}, {self.time_start_2}); No end detected"
        else:
            text = f"vel:{self.velocity:03.02}, len: {self.length:03.02}"
        return text

    @property
    def is_complete(self) -> bool:
        """
        A slug is complete when both sensors have start and end times.
        """
        return not (self.time_end_1 is None or self.time_end_2 is None or self.time_start_2 is None)

    @property
    def velocity(self) -> float | None:
        """
        Velocity (cm/s) computed from sensor spacing and average of start/end offsets.

        Returns
        -------
        float | None
        """
        if self._velocity is None:
            if not self.is_complete:
                return None
            self._velocity = self.sensor_spacer / \
                             ((self.time_start_2 - self.time_start_1 + self.time_end_2 - self.time_end_1) / 2)

        return self._velocity

    @property
    def length(self) -> float | None:
        """
        Length (cm) computed as velocity × average duration across both sensors.

        Returns
        -------
        float | None
        """
        if self._length is None:
            if self.velocity is None:
                return None
            self._length = self.velocity * (self.time_end_1 - self.time_start_1 + self.time_end_2 - self.time_start_2)/2

        return self._length


def extract_transitions(data: array_bool_n):
    """
    Find indices where a boolean (0/1) vector transitions up (0→1) or down (1→0).

    Parameters
    ----------
    data : array_bool_n
        Boolean-like 1D input (typed as in original)

    Returns
    -------
    np.ndarray
        Indices where transitions occur.
    """
    # Find transitions from 0 to 1 (0 -> 1)
    shifted_states = np.roll(data, 1)
    ups = (data == 1) & (shifted_states == 0)  # puts True on first True after rise
    # Find transitions from 1 to 0 (1 -> 0)
    downs = (data == 0) & (shifted_states == 1)  # puts True on first False after dip

    return np.where(ups | downs)[0]  # index of transitions


def edges_to_slugs(data: np.ndarray) -> list[Slug]:
    """
    Convert 2-channel binary edge activity into a list of `Slug` objects.

    Parameters
    ----------
    data : np.ndarray
        Expect shape (N, 3): time in col 0, binary sensor1 in col 1, binary sensor2 in col 2.

    Returns
    -------
    list[Slug]
        Detected slugs from rising/falling edges across the two channels.

    Notes
    -----
    - Some commented-out indexing preserves a previous downsample window idea.
    - This routine handles overlapping/incomplete slugs via `slug_buffer2`.
    """
    # index = np.concatenate((extract_transitions(data[:, 1]), extract_transitions(data[:, 2])))
    # index.sort()
    # index = np.concatenate((index, index - 1, index + 1))
    # index.sort()
    # data = data[index, :]

    slugs = []
    slug_buffer = None
    slug_buffer2 = None
    for i in range(1, data.shape[0]):
        if data[i, 1] - data[i - 1, 1] == 1:
            if slug_buffer is not None:
                if slug_buffer2 is None:
                    slug_buffer2 = slug_buffer
                else:
                    slugs.append(slug_buffer2)  # not complete

            slug_buffer = Slug(data[i, 0])

        elif data[i, 1] - data[i - 1, 1] == -1:
            if slug_buffer is None:
                continue
            slug_buffer.time_end_1 = data[i, 0]
        elif data[i, 2] - data[i - 1, 2] == 1:
            if slug_buffer is None:
                continue
            slug_buffer.time_start_2 = data[i, 0]
        elif data[i, 2] - data[i - 1, 2] == -1:
            if slug_buffer is None:
                continue
            if slug_buffer2 is not None:
                slug_buffer2.time_end_2 = data[i, 0]
                slugs.append(slug_buffer2)
                slug_buffer2 = None
            else:
                slug_buffer.time_end_2 = data[i, 0]
                slugs.append(slug_buffer)
                slug_buffer = None

    if slug_buffer is not None:
        slugs.append(slug_buffer)
    if slug_buffer2 is not None:
        slugs.append(slug_buffer2)

    return slugs


def print_slug_data(slugs: list[Slug]):
    """
    Print slug list with simple statistics (mean velocity and length).
    """
    for i, slug in enumerate(slugs):
        print(i, slug)

    print()
    print("avg. velocity: ", np.mean([slug.velocity for slug in slugs]))
    print("avg. length: ", np.mean([slug.length for slug in slugs]))


def main():
    """
    CLI entry: load CSV, preprocess, threshold to binary, extract/plot.

    Workflow
    --------
    - Load "data_0.csv" (first column assumed to be time).
    - Time-normalize to start at 0.
    - Apply exponential smoothing (typo preserved: `exponatial_filter` call).
    - Convert channels 1 and 2 to binary masks using mid-point thresholds.
    - (Optionally) extract slugs and print stats (commented).
    - Launch the PyQt window for plotting.
    """
    data = np.genfromtxt("data_0.csv", delimiter=",")
    data[:, 0] = data[:, 0] - data[0, 0]

    # exponential
    # NOTE: The function is defined as `exponential_filter`, but called here as `exponatial_filter`
    data_proc = exponatial_filter(data, 0.9)

    data_proc[:, 1] = convert_to_binary(
        data_proc[:, 1],
        (np.max(data_proc[:, 1]) - np.min(data_proc[:, 1])) / 2 + np.min(data_proc[:, 1])
    )
    data_proc[:, 2] = convert_to_binary(
        data_proc[:, 2],
        (np.max(data_proc[:, 2]) - np.min(data_proc[:, 2])) / 2 + np.min(data_proc[:, 2])
    )

    # slugs = edges_to_slugs(data_proc)
    # print_slug_data(slugs)

    # figure
    window()


def window():
    """
    Launch the PyQt application and show the main window.
    """
    app = QApplication([])
    win = MyWindow(app)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()