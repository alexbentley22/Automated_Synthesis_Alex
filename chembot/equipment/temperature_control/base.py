import abc
import logging

from unitpy import Quantity

from chembot.configuration import config
from chembot.equipment.equipment import Equipment


logger = logging.getLogger(config.root_logger_name + ".lights")


class TempControl(Equipment, abc.ABC):
    """
    Abstract base class for any *temperature‑controlling device* in Chembot.

    Purpose
    -------
    - Defines the minimal interface required for temperature‑control equipment.
    - Ensures that any subclass (heater, chiller, PID controller, thermal block,
      IR lamp, etc.) implements consistent read/write methods.
    - Integrates with the broader `Equipment` infrastructure, allowing lifecycle
      hooks, RabbitMQ messaging, logging, and state tracking.

    Expected Behavior
    -----------------
    Concrete subclasses must:
        • Accept a temperature set point (Quantity)
        • Report the current set point
        • Report the current measured temperature

    Why an abstract base class?
    ---------------------------
    This enforces a unified API so higher‑level Chembot workflows can interact
    with temperature control devices *without knowing the specific hardware type*.
    """

    @abc.abstractmethod
    def write_set_point(self, temperature: Quantity):
        """
        Write the target temperature for the device.

        Parameters
        ----------
        temperature : Quantity
            Unit‑safe temperature (e.g., 25 * Unit("degC")).

        Notes
        -----
        - Behavior depends on subclass: may send a serial command, publish a
          RabbitMQ message, update a PID loop, or control a heater/chiller.
        """
        pass

    @abc.abstractmethod
    def read_set_point(self) -> Quantity:
        """
        Read back the device’s configured set point.

        Returns
        -------
        Quantity
            The last commanded target temperature.

        Notes
        -----
        - Some hardware records this internally,
          others must store the last commanded value in the subclass.
        """
        pass

    @abc.abstractmethod
    def read_temperature(self) -> Quantity:
        """
        Read the *actual* measured temperature from the device.

        Returns
        -------
        Quantity
            The current measured temperature.

        Notes
        -----
        - Subclass must implement communication with the physical sensor
          (e.g., thermocouple, RTD, thermistor).
        - This is distinct from `read_set_point()` because the device may not have
          reached the target temperature yet.
        """
        pass