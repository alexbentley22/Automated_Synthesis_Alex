import logging

from unitpy import Quantity

from chembot.reference_data.pico_pins import PicoHardware
from chembot.configuration import config
from chembot.equipment.lights.light import Light
from chembot.rabbitmq.messages import RabbitMessageAction
from chembot.communication.serial_pico import PicoSerial

logger = logging.getLogger(config.root_logger_name + ".lights")


class LightPico(Light):
    """
    Light device controlled by a Raspberry Pi Pico via RabbitMQ.

    Purpose
    -------
    - Implements the `Light` abstract interface for a light driven by a Pico GPIO pin.
    - Supports ON/OFF and variable power via:
        * digital write (1 → full power, 0 → off),
        * PWM write for intermediate intensity (duty 0..65535 @ frequency).

    Integration
    -----------
    - Inherits `Light` → provides `write_on()` and `write_off()` at the Equipment layer.
    - Uses `RabbitMessageAction` to send commands to the Pico (through your Rabbit/RPC bridge).
    - Validates pins and frequency with `PicoHardware` helpers.

    Attributes
    ----------
    name : str
        Equipment name (used for routing/logging).
    communication : str
        RabbitMQ destination / communication route (e.g., `NamesSerial.PICO2`).
    pin : int
        Pico GPIO pin number used to drive the light output.
    color : Quantity | None
        Optional color descriptor (e.g., 470 nm as a `Quantity`) for metadata/reporting.
    frequency : int
        PWM frequency (Hz); validated and clamped to [100..50_000].
    power : int
        Current power setting [0..65535]; 0 = off, 65535 = full power.

    Notes
    -----
    - ON/OFF use digital writes for efficiency.
    - Intermediate power uses PWM with `duty=power` and configured `frequency`.
    """

    def __init__(self,
                 name: str,
                 communication: str,
                 pin: int,
                 color: Quantity | None = None,
                 frequency: int = 10_000,
                 ):
        super().__init__(name)
        self.color = color
        self.communication = communication

        # Pin (validated by PicoHardware)
        self._pin = None
        self.pin = pin

        # PWM frequency (validated, integer, range-checked)
        self._frequency = None
        self.frequency = frequency

        # Current stateful power value [0..65535]
        self.power: int = 0

        # Expose attributes for Equipment inspection / dashboards
        self.attrs += ["color", "communication", "pin", "frequency"]
        self.update += ["power"]

    # -------------------------
    # Properties (validated)
    # -------------------------

    @property
    def pin(self) -> int:
        """
        Pico GPIO pin used to drive the light output.
        """
        return self._pin

    @pin.setter
    def pin(self, pin: int):
        """
        Validate and set the Pico GPIO pin.
        """
        PicoHardware.validate_GPIO_pin(pin)
        self._pin = pin

    @property
    def frequency(self) -> int:
        """
        PWM frequency (Hz) used for variable power output.
        """
        return self._frequency

    @frequency.setter
    def frequency(self, frequency: int):
        """
        Validate and set the PWM frequency.

        Raises
        ------
        TypeError
            If frequency is not an int.
        ValueError
            If frequency is not in [100..50_000].
        """
        PicoHardware.validate_pwm_frequency(frequency)
        if not isinstance(frequency, int):
            raise TypeError("'frequency' must be an integer.")
        if not (100 < frequency < 50_000):
            raise ValueError("'frequency' must be between [100, 50_000]")
        self._frequency = frequency

    # -------------------------
    # Light abstract overrides
    # -------------------------

    def _write_on(self):
        """
        Hardware-specific ON: set full power (digital HIGH or PWM full).
        """
        self.write_power(65535)

    def _write_off(self):
        """
        Hardware-specific OFF: set zero power (digital LOW).
        """
        self.write_power(0)

    # -------------------------
    # Equipment lifecycle hooks
    # -------------------------

    def _activate(self):
        """
        Activation-time sanity ping of the target Pico connection, then defer to base.
        """
        # Verify the remote endpoint responds to a basic action (read_name).
        message = RabbitMessageAction(self.communication, self.name, "read_name")
        self.rabbit.send_and_consume(message, error_out=True)
        super()._activate()

    def _stop(self):
        """
        Stop-time behavior: ensure the light is off.
        """
        self._write_off()

    # -------------------------
    # Read/Write accessors
    # -------------------------

    def read_color(self) -> Quantity:
        """Return configured color (if any)."""
        return self.color

    def read_communication(self) -> str:
        """Return the communication route / destination name."""
        return self.communication

    def write_communication(self, communication: str):
        """
        Update the communication route / destination.

        Parameters
        ----------
        communication : str
            Communication port / route (e.g., 'COM9' or a logical name).
        """
        self.communication = communication

    def read_pin(self) -> int:
        """Return the Pico GPIO pin number."""
        return self.pin

    def write_pin(self, pin: int):
        """
        Update the Pico GPIO pin used for this light.

        Parameters
        ----------
        pin : int
            Pico GPIO pin; validated by PicoHardware.
            range: [0:1:27]
        """
        self.pin = pin

    def read_frequency(self) -> int:
        """Return the PWM frequency in Hz."""
        return self.frequency

    def write_frequency(self, frequency: int):
        """
        Update the PWM frequency.

        Parameters
        ----------
        frequency : int
            PWM frequency in Hz.
            range: [100:1:50_000]
        """
        self.frequency = frequency

    # -------------------------
    # Core power control
    # -------------------------

    def write_power(self, power: int):
        """
        Set light intensity / power, and forward the corresponding command to the Pico.

        Parameters
        ----------
        power : int
            Light intensity in [0..65535].
            - 0 → OFF (digital LOW)
            - 65535 → FULL (digital HIGH)
            - 1..65534 → PWM (duty=power, frequency=self.frequency)

        Behavior
        --------
        - Updates Equipment state: RUNNING when power>0, STANDBY when power==0.
        - Issues a RabbitMessageAction to the Pico:
            * PicoSerial.write_digital for 0 or 65535
            * PicoSerial.write_pwm for intermediate power values
        """
        # Update state machine
        if power > 0:
            self.state = self.states.RUNNING
        elif power == 0:
            self.state = self.states.STANDBY

        # Cache power locally
        self.power = power

        # Select appropriate Pico action based on power
        if self.power == 65535:
            # Full power → digital HIGH
            param = {"pin": self.pin, "value": 1}
            message = RabbitMessageAction(self.communication, self.name, PicoSerial.write_digital, param)
        elif self.power == 0:
            # Off → digital LOW
            param = {"pin": self.pin, "value": 0}
            message = RabbitMessageAction(self.communication, self.name, PicoSerial.write_digital, param)
        else:
            # Intermediate → PWM (duty and frequency)
            param = {"pin": self.pin, "duty": self.power, "frequency": self.frequency}
            message = RabbitMessageAction(self.communication, self.name, PicoSerial.write_pwm, param)

        # Fire-and-forget send (no immediate reply required)
        self.rabbit.send(message)

    def _deactivate(self):
        """
        Deactivation tail: force light LOW and set a watchdog to confirm the command executed.

        Notes
        -----
        - Sends a final digital LOW to ensure the output is off.
        - Arms a watchdog for the message (5 s) to verify it is acknowledged.
        """
        # Force off on the Pico side
        param = {"pin": self.pin, "value": 0}
        message = RabbitMessageAction(self.communication, self.name, PicoSerial.write_digital, param)
        self.rabbit.send(message)

        # Track for acknowledgement via watchdog
        self.watchdog.set_watchdog(message, 5)