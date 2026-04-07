import logging

from chembot.configuration import config
from chembot.equipment.valves.valve_configuration import ValveConfiguration, ValvePosition
from chembot.equipment.valves.base_valve import Valve
from chembot.communication.serial_pico import PicoSerial
from chembot.reference_data.pico_pins import PicoHardware
from chembot.rabbitmq.messages import RabbitMessageAction

logger = logging.getLogger(config.root_logger_name + ".valve")


class ValveServo(Valve):
    """
    Servo‑controlled valve implementation for Chembot.

    Purpose
    -------
    - Drive a rotary valve using a standard PWM‑controlled hobby servo or industrial
      micro‑servo attached to a Raspberry Pi Pico.
    - Each valve position must define a servo-specific **PWM duty setting**.
      (Stored inside `ValvePosition.setting`)
    - The Pico receives PWM commands via RabbitMQ → PicoSerial.

    Servo Behavior Notes
    --------------------
    - PWM frequency typically ~50 Hz (20 ms cycle).
    - Hobby servo pulse widths:
          0.5 ms  → one extreme
          1.5 ms  → mid‑position
          2.5 ms  → opposite extreme
    - Example conversion (16‑bit timer on Pico):
          duty = int(65535 / 20 ms * pulse_width_ms)

      Examples in comments:
          0:   ~1638
          90:  ~3932
          180: ~6225
          270: ~8191

    Requirements
    ------------
    - All positions in the configuration must specify a `.setting` value
      corresponding to the appropriate PWM duty for that position.
    """

    def __init__(
        self,
        name: str,
        communication: str,
        configuration: ValveConfiguration,
        pin: int,
        frequency: int = 50
    ):
        """
        Parameters
        ----------
        name : str
            Device name used by Chembot.
        communication : str
            RabbitMQ destination name for communicating with the Pico.
        configuration : ValveConfiguration
            Valve geometry and position list.
        pin : int
            Pico GPIO pin used for PWM output.
        frequency : int, optional
            PWM frequency in Hz (default 50 Hz).
        """
        super().__init__(name, configuration)

        self.communication = communication

        # Pin validation handled through property setter
        self._pin = None
        self.pin = pin

        # Frequency validation through setter
        self._frequency = None
        self.frequency = frequency

    # -------------------------
    # Configuration Properties
    # -------------------------

    @property
    def pin(self) -> int:
        """GPIO pin number used for PWM."""
        return self._pin

    @pin.setter
    def pin(self, pin: int):
        # Validate pin is a valid Pico GPIO
        PicoHardware.validate_GPIO_pin(pin)
        self._pin = pin

    @property
    def frequency(self) -> int:
        """PWM frequency in Hz."""
        return self._frequency

    @frequency.setter
    def frequency(self, frequency: int):
        # Validate PWM frequency range provided by PicoHardware
        PicoHardware.validate_pwm_frequency(frequency)

        if not isinstance(frequency, int):
            raise TypeError("'frequency' must be an integer.")

        if not (10 <= frequency <= 50_000):
            # Note: error text suggests lower bound 100, but logic checks 10.
            raise ValueError("'frequency' must be between [100, 50_000]")

        self._frequency = frequency

    # -------------------------
    # Lifecycle Hooks
    # -------------------------

    def _activate(self):
        """
        Ensure communication link is alive, then activate base valve behavior.

        Steps
        -----
        1. Send a harmless 'read_name' request to verify the Pico responds.
        2. Ensure all valve positions contain servo settings (duty cycle).
        3. Call base class `_activate()` to move to default position.
        """
        # Verify the communication target is reachable
        message = RabbitMessageAction(self.communication, self.name, "read_name")
        self.rabbit.send_and_consume(message, error_out=True)

        # Ensure all position objects contain PWM settings
        for pos in self.configuration.positions:
            if pos.setting is None:
                raise ValueError("Set servo settings in configuration positions settings.")

        super()._activate()

    def _stop(self):
        """Stop hook (currently no servo-specific stop behavior)."""
        pass

    # -------------------------
    # Hardware Movement
    # -------------------------

    def _move(self, position: ValvePosition):
        """
        Move valve to the given ValvePosition using PWM command.

        Behavior
        --------
        Sends an asynchronous RabbitMQ→Pico message containing:
            • pin       (GPIO pin)
            • duty      (pulse width → servo angle)
            • frequency (PWM frequency)
        """
        message = RabbitMessageAction(
            destination=self.communication,
            source=self.name,
            action=PicoSerial.write_pwm,
            kwargs={"pin": self.pin, "duty": position.setting, "frequency": self.frequency}
        )
        self.rabbit.send(message)

    # -------------------------
    # User-facing Read/Write Helpers
    # -------------------------

    def read_pin(self) -> int:
        """Return the GPIO pin used for PWM."""
        return self.pin

    def write_pin(self, pin: int):
        """
        Change GPIO pin.

        Parameters
        ----------
        pin : int
            Must be a valid Pico GPIO pin.
        """
        self.pin = pin

    def read_communication(self) -> str:
        """Return RabbitMQ destination string."""
        return self.communication

    def write_communication(self, communication: str):
        """
        Change communication channel.

        Parameters
        ----------
        communication : str
            New RabbitMQ destination (e.g., "pico_main")
        """
        self.communication = communication

    def read_frequency(self) -> int:
        """Return PWM frequency."""
        return self.frequency

    def write_frequency(self, frequency: int):
        """
        Change PWM frequency.

        Parameters
        ----------
        frequency : int
            Required range: [100, 50_000 Hz]
        """
        self.frequency = frequency

    # -------------------------
    # Deactivation
    # -------------------------

    def _deactivate(self):
        """
        On shutdown:
        - Drive pin LOW using PicoSerial.write_digital
        - Register message for watchdog monitoring
        """
        param = {"pin": self.pin, "value": 0}
        message = RabbitMessageAction(self.communication, self.name, PicoSerial.write_digital, param)
        self.rabbit.send(message)

        # Register message for timeout/watchdog
        self.watchdog.set_watchdog(message, 5)