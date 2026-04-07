import logging

from unitpy import Quantity

from chembot.reference_data.pico_pins import PicoHardware
from chembot.configuration import config
from chembot.rabbitmq.messages import RabbitMessageAction
from chembot.communication.serial_pico import PicoSerial

from chembot.equipment.sensors.sensor import Sensor

logger = logging.getLogger(config.root_logger_name + ".temperature")


class TemperaturePICO(Sensor):
    """
    Minimal wrapper for reading the Raspberry Pi Pico’s **internal temperature sensor**
    using the Chembot device framework and RabbitMQ-based communication.

    Purpose
    -------
    - Provide a lightweight `Sensor` implementation for cases where the Pico itself
      acts as the temperature probe (using its built-in ADC + diode junction).
    - Integrate with Chembot's RabbitMQ messaging so that the host can request a
      temperature reading asynchronously.
    - Return the temperature as a unit-aware `Quantity` value.

    Notes
    -----
    - This sensor **does not** use a thermistor or external circuit.
    - This is the "quick and simple" temperature source from the microcontroller.
    - The actual temperature conversion is done on the Pico side via
      `PicoSerial.read_internal_temperature`.
    """

    def __init__(
        self,
        name: str,
        communication: str,
    ):
        """
        Parameters
        ----------
        name : str
            Logical name of this temperature sensor instance.
        communication : str
            RabbitMQ destination name for communicating with the Pico controller.

        Notes
        -----
        - `PicoHardware.pin_internal_temp` refers to the Pico's dedicated internal
          temperature-sensing ADC channel.
        """
        super().__init__(name)
        self.communication = communication
        self._pin = PicoHardware.pin_internal_temp

    # -------------------------
    # Lifecycle hooks (unused)
    # -------------------------

    def _activate(self):
        """Activation hook — not required for Pico internal temperature."""
        pass

    def _deactivate(self):
        """Deactivation hook — not required for Pico internal temperature."""
        pass

    def _stop(self):
        """Emergency stop hook — not required for Pico internal temperature."""
        pass

    # -------------------------
    # Core measurement action
    # -------------------------

    def write_measure(self) -> Quantity:
        """
        Request a temperature reading from the Pico and return it.

        Workflow
        --------
        1. Build a RabbitMQ `RabbitMessageAction` targeting the Pico's
           `read_internal_temperature` handler.
        2. Send message and wait (blocking) for the Pico's reply.
        3. Return the reported `Quantity` temperature value.

        Returns
        -------
        Quantity
            Temperature returned by the Pico (typically in °C).

        Raises
        ------
        Exception via RabbitMQ
            If communication or decoding fails.
        """
        message = RabbitMessageAction(
            self.communication,
            self.name,
            PicoSerial.read_internal_temperature
        )
        reply = self.rabbit.send_and_consume(message, error_out=True)
        return reply.value