# -*- coding: utf-8 -*-
"""
Various methods of drawing scrolling plots.

This example creates a window with a single plot that scrolls in time.
New data points are appended in small "chunks"; each chunk is drawn as its
own curve and translated leftward over time to create a live-scrolling effect.

Key idea:
- We maintain multiple curve objects (each representing up to `chunkSize` samples).
- As time advances, we shift all curves left (by updating their X position).
- When starting a new chunk, we create a new curve and reuse the last sample
  of the previous chunk to avoid visual gaps.
- We drop old curves once we have more than `maxChunks`, keeping memory bounded.
"""

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui  # noqa: F401 (imported for completeness in typical pg examples)
import numpy as np

# Create a graphics window that can hold multiple plots in a grid
win = pg.GraphicsLayoutWidget(show=True)
win.setWindowTitle('pyqtgraph example: Scrolling Plots')

# -----------------------------
# Configuration for chunked plot
# -----------------------------
chunkSize = 100        # Number of samples per curve "chunk"
maxChunks = 10         # Maximum number of chunk curves to keep (oldest are removed)
startTime = pg.ptime.time()  # Reference start time for x-axis (seconds since some epoch)

# Add a new row in the layout and place our plot there
win.nextRow()
p5 = win.addPlot(colspan=2)  # single plot spanning two columns (for layout symmetry in examples)
p5.setLabel('bottom', 'Time', 's')  # x-axis label
p5.setXRange(-10, 0)  # Show the last 10 seconds (range from -10 to 0, where 0 is 'now')

# Keep track of the chunk curves currently displayed
curves = []

# Working buffer that holds the time/value pairs for the current chunk
# Shape: (chunkSize+1, 2) so we can prepend the last point from the previous chunk
data5 = np.empty((chunkSize + 1, 2))
ptr5 = 0  # Global sample counter (advances every timer tick)


def update3():
    """
    Update function for the chunked scrolling plot.

    Behavior:
    - Translates all existing curves left to simulate time passing, by setting their x-offset
      to -(now - startTime). Newest time is always near x=0; older data moves toward -10.
    - Appends one new sample to the current chunk buffer.
    - When the current chunk fills, starts a new curve (chunk) and carries over the last point
      to avoid visual discontinuities.
    - Removes oldest chunks if we exceed `maxChunks`.
    """
    global p5, data5, ptr5, curves

    now = pg.ptime.time()

    # Translate existing curves left so x=0 is "now"
    # This is cheaper than recomputing all x-data for all curves every frame.
    for c in curves:
        c.setPos(-(now - startTime), 0)

    # Determine index within the current chunk
    i = ptr5 % chunkSize

    if i == 0:
        # Starting a new chunk:
        # 1) Create a new curve and append to the list
        # 2) Preserve the last sample from the previous chunk
        curve = p5.plot()
        curves.append(curve)

        # Carry the last point forward so the new chunk starts where the previous ended.
        last = data5[-1]
        data5 = np.empty((chunkSize + 1, 2))
        data5[0] = last

        # If we have too many chunks, remove the oldest from the scene and the list
        while len(curves) > maxChunks:
            c = curves.pop(0)
            p5.removeItem(c)
    else:
        # Continue plotting into the most recent curve
        curve = curves[-1]

    # Append a new sample into the current chunk:
    # x = elapsed time since start; y = some random signal sample
    data5[i + 1, 0] = now - startTime
    data5[i + 1, 1] = np.random.normal()

    # Update only the current curve's visible segment (up to i+2 points in the buffer)
    curve.setData(x=data5[:i + 2, 0], y=data5[:i + 2, 1])

    # Advance global sample counter
    ptr5 += 1


# Orchestrator that could call multiple update functions if needed.
def update():
    """
    Master update function called by the timer.

    In more complex examples, this could update several plots. Here it just
    calls `update3()` to advance our chunked scrolling plot.
    """
    update3()


# QTimer triggers the update function at a fixed interval (every 10 ms ~ 100 FPS request)
timer = pg.QtCore.QTimer()
timer.timeout.connect(update)
timer.start(10)

if __name__ == '__main__':
    # Start/enter the Qt application event loop so the window remains responsive
    pg.mkQApp().exec_()