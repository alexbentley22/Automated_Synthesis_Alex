# This module checks the status of a normally-open/normally-closed end stop (limit switch)
# and returns 1 (HIGH) when it is pushed/active or 0 (LOW) when it is not.
#
# Hardware assumptions
# --------------------
# - Raspberry Pi GPIO input pin is wired to a mechanical end-stop (limit switch).
# - Pull-up/pull-down configuration is *not* set here; you must ensure the input
#   does not float (use external resistor or set pull_up_down in GPIO.setup if needed).
# - BOARD numbering mode is used (pins labeled 1..40 on the header), not BCM numbering.

from time import sleep
import RPi.GPIO as GPIO


# Use physical board pin numbering (1..40) to match silkscreen labels on the header.
GPIO.setmode(GPIO.BOARD)


class EndStop:
    """
    Simple wrapper for reading an end stop (limit switch) via a single GPIO input.

    Parameters
    ----------
    pin : int
        The physical BOARD pin number (1..40) connected to the end stop signal.

    Notes
    -----
    - This class does not set an internal pull-up or pull-down. If your switch
      does not have external resistors, consider adding:
          GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
      or
          GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
      depending on your wiring.
    - The return value of `check_status()` is whatever `GPIO.input()` reads:
      1 (HIGH) or 0 (LOW). Whether “pushed” reads as 1 or 0 depends on your wiring.
    """

    def __init__(self, pin: int):
        self.pin = pin  # store the physical BOARD pin number
        # Configure pin as input. Add pull_up_down if your circuit requires it.
        GPIO.setup(self.pin, GPIO.IN)

    def check_status(self) -> int:
        """
        Read the current logic level of the end stop input.

        Returns
        -------
        int
            1 if the pin is HIGH, 0 if the pin is LOW.

        Interpretation
        --------------
        - If wired with a pull-up and switch to ground (common pattern):
            * Not pressed  → reads 1 (because pulled up)
            * Pressed      → reads 0 (shorted to GND)
          In that case, you may want to invert in your application logic.
        - If wired with a pull-down and switch to 3V3:
            * Not pressed  → reads 0
            * Pressed      → reads 1
        """
        return GPIO.input(self.pin)


def cleanup():
    """
    Reset all GPIO channels that have been used by this program.

    Best practice
    -------------
    Call this in a finally-block or program shutdown path so the GPIO
    subsystem is left in a clean state and pins return to default mode.
    """
    GPIO.cleanup()


if __name__ == "__main__":
    # Simple standalone test: sample an end stop state multiple times and print it.
    try:
        # Example hardware hookup: use BOARD pin 36 (physical header pin).
        end_stop = EndStop(36)

        # Small delay after setup, useful if external circuits need to settle.
        sleep(0.5)

        for i in range(25):
            # Prints 1 for HIGH, 0 for LOW. Whether that means “pushed” depends on wiring.
            print(end_stop.check_status())
            sleep(0.2)

    finally:
        # Always clean up the GPIO on exit.
        cleanup()
        print('Program done')