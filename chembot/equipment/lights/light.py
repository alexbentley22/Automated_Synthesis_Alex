import abc
import logging

from chembot.configuration import config
from chembot.equipment.equipment import Equipment


logger = logging.getLogger(config.root_logger_name + ".lights")


class Light(Equipment, abc.ABC):
    """
    Abstract base class for light devices in the Chembot system.

    Purpose
    -------
    - Provides a simple, consistent interface for turning a light **on** (full power)
      and **off**, while integrating with the `Equipment` lifecycle and state model.
    - Concrete subclasses implement the hardware-specific behavior in `_write_on()` and `_write_off()`.

    Integration
    -----------
    - Inherits from `Equipment`, so it participates in:
        * activation/deactivation via RabbitMQ infrastructure,
        * message dispatch (read/write actions),
        * state reporting (e.g., STANDBY, RUNNING).
    - Typical concrete implementations might control:
        * a GPIO-driven LED,
        * a PWM-controlled driver,
        * a serial-controlled light source,
        * or a networked illumination device.

    Notes
    -----
    - This base interface intentionally exposes only **full on/off**. If your device
      supports brightness levels, colors, or patterns, add additional write methods
      (e.g., `write_power(power: int)`, `write_brightness(level: float)`, `write_color(r,g,b)`)
      in your subclass (and/or in a richer abstract base for such lights).
    """

    def write_on(self):
        """
        Public action: Turn the light **on at full power**.

        Behavior
        --------
        - Sets equipment state to RUNNING.
        - Calls the hardware-specific `_write_on()` implemented by subclasses.

        Notes
        -----
        - If your device needs a warm-up or a confirmation step, handle it in `_write_on()`
          and raise/log errors if the device does not reach the desired state.
        """
        self.state = self.states.RUNNING
        self._write_on()

    def write_off(self):
        """
        Public action: Turn the light **off**.

        Behavior
        --------
        - Calls the hardware-specific `_write_off()` implemented by subclasses.
        - Sets equipment state back to STANDBY.

        Notes
        -----
        - If your device supports a safe shutdown (e.g., ramp-down), implement it in `_write_off()`.
        """
        self._write_off()
        self.state = self.states.STANDBY

    # -------------------------
    # Hardware-specific methods
    # -------------------------

    @abc.abstractmethod
    def _write_on(self):
        """
        Hardware-specific implementation to turn the light on (full power).

        Must be implemented by concrete subclasses to perform the actual I/O,
        such as toggling GPIO, writing PWM, sending a serial command, or calling
        a driver API.
        """
        ...

    @abc.abstractmethod
    def _write_off(self):
        """
        Hardware-specific implementation to turn the light off.

        Must be implemented by concrete subclasses to perform the actual I/O.
        """
        ...