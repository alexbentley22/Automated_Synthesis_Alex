"""
Real-time plotting UI for reference data streamed from a serial-connected device.

Overview
--------
- Launches a PyQt application window using pyqtgraph's DockArea layout.
- Creates one main plot with up to 6 signal lines, updated in real time.
- Reads new samples by calling a globally available `in_phase_sensor` object
  (created in `main()`), which must provide:
      - `.measure_mean(smooth=False)` → returns an iterable like:
            [timestamp_seconds, y1, y2, y3, y4, y5, y6]
      - `.get_mean()` to initialize internal averaging/buffering (as used below).
- Maintains fixed-length buffers using numpy.roll for a smooth scrolling effect.
- Shows simple UI controls (Start/Stop buttons — not yet wired) and live FPS/counter labels.

Notes
-----
- The code assumes exactly 6 traces (lines) are returned after the timestamp.
- You can change `self.num_lines` to match your device, but also update color_list accordingly.
- The code uses `exec` to create and update plot line attributes (`self.w1_plot_{i}`) for brevity.
  If you prefer safer patterns, replace with a list of plot curves.

Serial device expectations
--------------------------
- A separate module (main_code.phase_sensor) must define a PhaseSensor class that
  exposes `.measure_mean()` and `.get_mean()` as used here.
- `main()` constructs the `in_phase_sensor` and points it at a COM port (Windows).
"""

import time
import sys
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import QApplication, QMainWindow
from pyqtgraph.dockarea import *  # DockArea, Dock
import pyqtgraph as pg
import numpy as np

# Fixes scaling issues across HiDPI monitors (optional, depends on platform/Qt build)
import os
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

# RGB colors for up to 6 traces (blue, red, green, orange, purple, yellow)
color_list = [
    [10, 36, 204],   # blue
    [172, 24, 25],   # red
    [6, 127, 16],    # green
    [251, 118, 35],  # orange
    [145, 0, 184],   # purple
    [255, 192, 0],   # yellow
]


class MyWindow(QMainWindow):
    """
    Main application window embedding a DockArea with:
      - Dock2: realtime plot of 6 channels vs time
      - Dock3: control panel with start/stop buttons and FPS/counter labels

    The view updates continuously via a QTimer, pulling the latest
    sample from a global sensor object: `in_phase_sensor.measure_mean(smooth=False)`.
    """

    def __init__(self, app):
        super(MyWindow, self).__init__()
        self.app = app

        # -------------------------
        # Window / Dock layout init
        # -------------------------
        self.area = DockArea()
        self.setCentralWidget(self.area)
        self.resize(1200, 800)
        self.setWindowTitle('Plotting reference_data from serial connection.')
        # pg.setConfigOptions(antialias=True) # Optional: nicer plots but slower

        # -------------------------
        # Menubar (placeholder demo)
        # -------------------------
        bar = self.menuBar()
        file = bar.addMenu("File")
        file.addAction("New")
        file.addAction("save")
        file.addAction("quit")

        # -------------------------
        # Docks
        # -------------------------
        self.d2 = Dock("Dock2", size=(1200, 600), hideTitle=True)   # main plot
        self.d3 = Dock("Dock3", size=(1200, 200), hideTitle=True)   # controls / labels
        # Place plot dock at top, control dock at bottom
        self.area.addDock(self.d2)
        self.area.addDock(self.d3, 'bottom', self.d2)

        # -------------------------
        # Dock 2: Plot setup
        # -------------------------
        self.w1 = pg.PlotWidget(title="Plot")
        self.d2.addWidget(self.w1)
        self.w1.setLabel('left', 'Signal', units='abs')
        self.w1.setLabel('bottom', 'Time', units='sec')

        # Number of signal lines and buffer length
        self.num_lines = 6
        self.plot_points = 100

        # Initialize data buffers:
        #   - x: time axis
        #   - y: shape (num_lines, plot_points)
        self.w1_xdata = np.zeros(self.plot_points)
        self.w1_ydata = np.zeros([self.num_lines, self.plot_points])

        # Create one curve per line.
        # NOTE: This uses exec for concise attribute creation. Alternatively,
        # keep a list: self.curves = [self.w1.plot(...), ...] and iterate over it.
        for i in range(self.num_lines):
            pen = pg.mkPen(color=color_list[i], width=3)
            exec(f"self.w1_plot_{i} = self.w1.plot(self.w1_xdata, self.w1_ydata[{i}, :], pen=pen)")

        # -------------------------
        # Dock 3: Buttons and labels
        # -------------------------
        self.w3 = pg.LayoutWidget()
        self.d3.addWidget(self.w3)

        # Buttons (not wired; add .clicked.connect(...) handlers as needed)
        self.b1 = QtWidgets.QPushButton('Start')
        self.b2 = QtWidgets.QPushButton('Stop')
        self.w3.addWidget(self.b1, row=0, col=0)
        self.w3.addWidget(self.b2, row=0, col=1)

        # FPS and loop counter labels
        self.l1 = pg.ValueLabel(siPrefix=True, suffix='fps')    # smoothed frames per second
        self.l2 = pg.ValueLabel(siPrefix=True, suffix='count')  # number of update loops
        self.w3.addWidget(self.l1, row=1, col=0)
        self.w3.addWidget(self.l2, row=1, col=1)

        # Initialize FPS state
        self.fps_time = time.time()
        self.fps = 0

        # -------------------------
        # Update timer
        # -------------------------
        self.w1_timer = pg.QtCore.QTimer()
        self.w1_timer.timeout.connect(self.update_all)
        # 0 ms requests "as fast as possible" updates; adjust if you need throttling
        self.w1_timer.start(0)
        self.counter = 0

    # -------------------------
    # Update / UI logic
    # -------------------------
    def update_all(self):
        """
        Master update handler called by the QTimer:
          - Updates plot buffers and redraws curves.
          - Refreshes FPS and counter labels ~every 10 cycles.
          - Processes Qt events to keep the UI responsive.
        """
        self.update_plots()
        if self.counter % 10 == 0:  # update labels at a reduced rate
            self.update_labels()
        self.app.processEvents()
        self.counter += 1

    def update_plots(self):
        """
        Scroll the plot by shifting buffers left and append the newest sample:
          - Uses numpy.roll to discard the oldest sample and make room.
          - Fetches the latest reading from `in_phase_sensor.measure_mean(smooth=False)`.
          - Assumes the first element is time (seconds), followed by `num_lines` values.
          - Updates each curve's data.
        """
        # Shift x and all y series left by 1 sample
        self.w1_xdata = np.roll(self.w1_xdata, -1)
        self.w1_ydata = np.roll(self.w1_ydata, -1)

        # Pull new sample from the global sensor instance
        global in_phase_sensor
        new_data = in_phase_sensor.measure_mean(smooth=False)
        # Example structure: [timestamp, ch1, ch2, ch3, ch4, ch5, ch6]

        # Append the newest time and channels
        self.w1_xdata[-1] = new_data[0]
        self.w1_ydata[:, -1] = new_data[1:]

        # Redraw each line with updated buffers
        for i in range(self.num_lines):
            exec(f"self.w1_plot_{i}.setData(self.w1_xdata, self.w1_ydata[{i}, :])")

    def update_labels(self):
        """
        Update FPS (exponentially smoothed) and loop counter labels.
        """
        now = time.time()
        dt = time.time() - self.fps_time
        self.fps_time = now
        a = 0.8  # smoothing factor (higher = more weight to recent FPS)
        # We update labels every 10 loops, so requested instantaneous FPS ~ (10 / dt)
        self.fps = a * (10 / dt) + (1 - a) * self.fps
        self.l1.setValue(self.fps)
        self.l2.setValue(self.counter)


def main():
    """
    Initialize the global sensor object used by MyWindow.

    Expectations:
    -------------
    - `main_code.phase_sensor` must define a `PhaseSensor` class with:
        - PhaseSensor(name, port, number_sensors)
        - .get_mean() : any initialization or initial mean calculation
        - .measure_mean(smooth=False) : returns [t, ch1..chN]
    - Adjust 'port' to the correct COM/tty for your system.
    """
    from main_code import phase_sensor
    global in_phase_sensor
    in_phase_sensor = phase_sensor.PhaseSensor(
        name="in_phase_sensor",
        port="COM7",
        number_sensors=6
    )
    # Optional: perform an initial mean measurement/buffer warm-up
    in_phase_sensor.get_mean()


def window():
    """
    Create the QApplication, construct and show the main window, and enter the Qt event loop.
    """
    app = QApplication(sys.argv)
    win = MyWindow(app)
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    # Initialize the sensor connection and launch the GUI
    main()
    window()