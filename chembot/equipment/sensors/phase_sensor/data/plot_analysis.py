import numpy as np
import matplotlib.pyplot as plt
import os
import re

from main_code.utils.plotting import color_list_norm
from main_code.sub_equip.phase_sensor.phase_sensor import PhaseSensor


# -----------------------------------------------------------------------------
# Batch loader and plotter for PhaseSensor CSV outputs
#
# Purpose
# -------
# - Discover CSV files that match a specific prefix pattern (e.g., "in_phase_sensor_001.csv").
# - Vertically stack all data into a single array (assumes same column schema).
# - Use two representative rows as "gas" and "liquid" references.
# - Call `PhaseSensor.determine_phase(...)` to classify phase per channel, per time.
# - Plot raw signals and phase results with consistent colors.
#
# Assumptions
# -----------
# - CSV schema: first column is time in microseconds; subsequent columns are channel readings.
# - Row 10 → representative gas reference; row 160 → representative liquid reference.
#   (Adjust to your dataset if necessary.)
# - All files share identical column counts and ordering.
# - Working directory contains the target CSVs (path pattern below uses `os.listdir("")`).
# -----------------------------------------------------------------------------

# -------------------------
# Discover and load CSV files
# -------------------------

file_prefix = "in_phase_sensor_"

# Find files named like: in_phase_sensor_<number>*.csv
files = [f for f in os.listdir("") if re.match(fr'^({file_prefix})[0-9]+.*\.(csv)$', f)]

# Vertically stack all CSV files (using vstack with first-file init pattern)
for file in files:
    try:
        data = np.vstack(  # noqa: F821 (data is created on the first iteration)
            (data, np.loadtxt(open(file, "rb"), delimiter=",", skiprows=1))
        )
    except NameError:
        # First file initializes the 'data' array
        data = np.loadtxt(open(file, "rb"), delimiter=",", skiprows=1)

# -------------------------
# Build phase references and classify
# -------------------------

# Choose representative rows for "gas" and "liquid" references.
# Note: These row indices depend on your acquisition; change if needed.
gas = np.array(data[10, 1:])     # row 10, all channels except time column
liq = np.array(data[160, 1:])    # row 160, all channels except time column

# Determine phase per sample and channel using PhaseSensor's classifier.
# Expected output shape: (N_samples, N_channels)
phase = PhaseSensor.determine_phase(data[:, 1:], gas, liq)

# -------------------------
# Plot raw signals and phase classification
# -------------------------

fig, (ax1, ax2) = plt.subplots(2, 1)

# Labels and axes
ax1.set_xlabel('time (sec)')
ax1.set_ylabel('signal')

# Plot each channel with a normalized, consistent color palette
for line, color in zip(range(data[0, 1:].size), color_list_norm):
    # time axis is in microseconds; convert to seconds
    ax1.plot(data[:, 0] / 1_000_000, data[:, line + 1], color=color)
    ax2.plot(data[:, 0] / 1_000_000, phase[:, line], color=color)

fig.tight_layout()  # otherwise the right y-label is slightly clipped
plt.show()