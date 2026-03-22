# test speed of timer
#
# Purpose
# -------
# Sweep the requested sleep delay across orders of magnitude and measure the
# *actual* achieved toggle period on a Raspberry Pi GPIO pin. This helps you
# understand the timing accuracy and lower-bound resolution of `time.sleep()`
# when used to generate software-timed pulses.
#
# Method
# ------
# - Use BOARD pin 40 as a digital output.
# - For each target delay (exp decay curve), toggle the pin 50 times with
#   sleep(delay/2) between rising and falling edges.
# - Measure elapsed wall time and compute the *average* actual delay per cycle.
# - Plot requested delay vs. measured delay on log–log axes along with y=x
#   reference to visualize deviations (jitter, minimum achievable sleep, etc.).
#
# Notes
# -----
# - This is *software* timing with `time.sleep()` and general-purpose Linux,
#   so OS scheduling will introduce jitter and a hard floor on minimum sleep
#   (often ~0.3–1 ms in Python, depending on OS, load, and Python version).
# - For precise/wideband timing, consider:
#     * pigpio / DMA-based PWM,
#     * kernel-space drivers, or
#     * offloading pulse generation to a microcontroller (e.g., RP2040/Pico).

from time import sleep, time
from math import exp
import matplotlib.pyplot as plt
import RPi.GPIO as GPIO

# -------------------------
# GPIO setup (BOARD numbering)
# -------------------------
GPIO.setmode(GPIO.BOARD)   # use physical header numbers 1..40
GPIO.setup(40, GPIO.OUT)   # set pin 40 as output
GPIO.output(40, 0)         # start low

# -------------------------
# Experiment parameters
# -------------------------
n = 75                     # number of target delay points to test
delay_time = [0] * n       # requested delays (s) for each test point
delay = [0] * n            # measured (actual) average delays (s) achieved

# -------------------------
# Sweep: generate target delays and measure actual timing
# -------------------------
for i in range(n):
    # Exponential decay from larger delays down toward very small ones.
    # The choice -(i+75)/15 packs many points at the short-delay end,
    # pushing the test into the region where sleep() floor dominates.
    delay_time[i] = exp(-(i + 75) / 15)

    start = time()
    # Generate 50 pulse cycles: HIGH (delay/2) then LOW (delay/2)
    for ii in range(50):
        GPIO.output(40, 1)
        sleep(delay_time[i] / 2)
        GPIO.output(40, 0)
        sleep(delay_time[i] / 2)

    # Average measured delay per cycle (not per half-cycle)
    delay[i] = (time() - start) / 50

# -------------------------
# Plot results
# -------------------------
plt.plot(delay_time, delay, label='Measured vs. requested')
# y=x reference diagonal (spanning relevant orders of magnitude)
plt.plot([0.0001, 2], [0.0001, 2], '--', label='Ideal (y = x)')

plt.xscale('log')
plt.yscale('log')
plt.xlabel("Requested delay (s)")
plt.ylabel("Measured average delay (s)")
plt.title("Software sleep timing on Raspberry Pi (GPIO toggle)")
plt.legend()
plt.grid(True, which='both', ls=':')
plt.show()