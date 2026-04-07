import functools
import pathlib
import logging
import time

from serial import Serial
import numpy as np
from unitpy import Unit, Quantity

from chembot.configuration import config, create_folder
from chembot.utils.threading_utils import timeout_wrapper
from chembot.equipment.sensors.sensor import Sensor
from chembot.utils.algorithms.change_detection import CUSUM
from chembot.rabbitmq.messages import RabbitMessageAction, RabbitMessageReply
from chembot.equipment.pumps.syringe_pump import SyringePump

logger = logging.getLogger(config.root_logger_name + ".phase_sensor")


def format_pin(pin: int, mode: int) -> str:
    """
    Format a `(pin, mode)` pair into the 3-character string the Pico expects.

    Parameters
    ----------
    pin : int
        ADC channel/pin number (0..N).
    mode : int
        Measurement mode per firmware (e.g., 0=raw/offset, 1=scaled/normal; device-specific).

    Returns
    -------
    str
        Zero-padded pin and mode: f"{pin:02}{mode}"
    """
    return f"{pin:02}{mode}"


def parse_measurement(message: str, dtype=np.int64) -> np.ndarray:
    """
    Parse a comma-separated measurement reply from the Pico.

    Parameters
    ----------
    message : str
        Raw CSV string (e.g., "123,456").
    dtype : dtype-like
        Target dtype for the resulting numpy array.

    Returns
    -------
    np.ndarray
        1D array of parsed values.
    """
    return np.array(message.split(","), dtype=dtype)


class Slug:
    """
    Container for a detected slug (liquid segment) traveling past two sensors.

    Purpose
    -------
    - Capture **event times** at sensor 1 and sensor 2 (rising/falling edges).
    - Derive **velocity**, **length**, **volume**, and **time offsets** from those events.
    - Keep computations **unit-safe** using `unitpy`.

    Notes
    -----
    - `sensor_spacer` and `tube_diameter` are engineering constants used to
      compute kinematics and volume; adjust to match your hardware.
    - Velocity is computed only when **complete** timing is available
      (both start/end on both sensors).
    """
    sensor_spacer = 0.95 * Unit.cm          # distance between sensor 1 and 2
    tube_diameter = 0.0762 * Unit.cm        # inner diameter of tubing at sensor

    __slots__ = ("time_start_1", "time_end_1", "time_start_2", "time_end_2", "_length", "_velocity")

    def __init__(self,
                 time_start_1: float | int,
                 time_end_1: float | int = None,
                 time_start_2: float | int = None,
                 time_end_2: float | int = None,
                 velocity: Quantity | None = None
                 ):
        self.time_start_1 = time_start_1
        self.time_end_1 = time_end_1
        self.time_start_2 = time_start_2
        self.time_end_2 = time_end_2
        self._length = None
        self._velocity = velocity

    def __str__(self):
        """
        Human-readable summary with velocity/length/volume when available.
        """
        if self.volume is not None:
            text = f"vel:{self.velocity:3.2f}, len: {self.length:3.2f}, vol:{self.volume:3.2f}"
        elif self.time_span:
            text = f"start: {self.time_start_1:3.2f}"
            if self.velocity is not None:
                text += f", velocity: {self.velocity:3.2f})"
        else:
            text = f"start: {self.time_start_1:3.2f}, No end detected"

        return text

    @property
    def time_span(self) -> Quantity | None:
        """
        Duration (s) that the slug triggered sensor 1 (end - start).

        Returns
        -------
        Quantity | None
            Time duration with units of seconds, or None if not complete.
        """
        if self.time_end_1 is None:
            return None
        t = self.time_end_1 - self.time_start_1
        if isinstance(t, Quantity):
            return t
        return t * Unit.s

    @property
    def time_offset(self):
        """
        Average offset between starts and ends across both sensors.

        Returns
        -------
        Quantity | None
            Average of (start_2 - start_1 + end_2 - end_1)/2 as a time Quantity,
            or None if the slug is incomplete.
        """
        if self.is_complete:
            t = (self.time_start_2 - self.time_start_1 + self.time_end_2 - self.time_end_1) / 2
            if isinstance(t, Quantity):
                return t
            return t * Unit.s
        return None

    @property
    def is_complete(self) -> bool:
        """
        Whether both sensors have observed a start and end for this slug.
        """
        return not (self.time_end_1 is None or self.time_end_2 is None or self.time_start_2 is None)

    @property
    def velocity(self) -> Quantity | None:
        """
        Linear velocity of the slug computed from the sensor spacing and time span.

        Returns
        -------
        Quantity | None
            Velocity in mm/s, or None if insufficient timing info.
        """
        if self._velocity is None:
            if not self.is_complete:
                return None
            self._velocity = self.sensor_spacer / self.time_span

        return self._velocity.to("mm/s")

    @property
    def length(self) -> Quantity | None:
        """
        Slug length along the tube (mm), computed from velocity × time_span.
        """
        if self._length is None:
            if self.velocity is None or self.time_span is None:
                return None
            self._length = self.velocity * self.time_span

        return self._length.to('mm')

    @property
    def volume(self) -> Quantity | None:
        """
        Slug volume (uL) as cylinder volume: length × π × (ID/2)^2.
        """
        if self.length is None:
            return None
        return (self.length * np.pi * (self.tube_diameter / 2) ** 2).to("uL")


class PhaseSensor(Sensor):
    """
    Inline **two-point optical phase sensor** driven by a Pico over serial.

    Purpose
    -------
    - Toggle sensor LEDs, configure **gain** and **offset voltage**, and capture raw ADC readings.
    - Provide a high-level **`write_measure()`** action that returns parsed numeric data.
    - Support **auto offset/gain** calibration and **slug detection** orchestration with pumps via RabbitMQ.

    Sign convention
    ---------------
    - The signal **increases** on **gas → liquid** transitions (per note in original code).
    """

    # ADC dtype and voltage scaling (per external ADC / gain chain)
    dtype = np.int16
    gains = {
        1: 4.096,
        2: 2.048,
        4: 1.024,
        8: 0.512,
        16: 0.256
    }

    # Default multiplexed inputs/modes used when sampling
    pins = (0, 1)
    modes = (0, 1)

    @property
    def _data_path(self):
        """
        Ensure and return the local data folder for phase sensor runs:
          <data_dir>/phase_sensor
        """
        path = config.data_directory / pathlib.Path("phase_sensor")
        create_folder(path)
        return path

    def __init__(
        self,
        name: str,
        port: str = "COM6",
        tube_diameter: Quantity = Quantity("0.03 inch"),
        # number_sensors: int = 2,
    ):
        """
        Parameters
        ----------
        name : str
            Logical device name.
        port : str
            Serial port for the Pico/firmware (e.g., 'COM6').
        tube_diameter : Quantity
            Inner diameter of the tubing at the sensor location.

        Raises
        ------
        ValueError
            If the serial port fails to initialize within the timeout wrapper.
        """
        super().__init__(name)
        self.serial = timeout_wrapper(functools.partial(Serial, port=port), 1)
        if self.serial is None:
            raise ValueError(f"{self.name}.serial not initializing. Try unplugging in cable and retry.")

        self.tube_diameter = tube_diameter
        self.number_sensors = 2
        self.gain = 1
        self.offset_voltage = 0
        self._led_on = False
        self._slug_finder = None

    def __repr__(self):
        return f"Phase Sensor\n\tclass_name: {self.name}\n\tstate: {self.state}"

    @property
    def led_on(self) -> bool:
        """Return whether the LED supply is currently enabled (software flag)."""
        return self._led_on

    def _write_and_read(
        self,
        message: str,
        expected_reply: str = None,
        reply_processing: callable = None,
        time_out: float | int = 0.2,
        retries: int = 3
    ):
        """
        Send a line command to the Pico and read a single reply line with retries.

        Parameters
        ----------
        message : str
            Command to send (without trailing newline).
        expected_reply : str | None
            If provided, the reply's first character must match this.
        reply_processing : callable | None
            A function that takes the reply string and returns parsed data.
        time_out : float | int
            Serial `timeout` in seconds for this call.
        retries : int
            Number of attempts before surfacing an error.

        Returns
        -------
        Any
            Either the raw reply string or the processed result.

        Raises
        ------
        ValueError
            If reply type/format is unexpected or parsing fails after retries.
        """
        # logger.debug(f"send: {message}")
        self.serial.timeout = time_out
        for i in range(retries):
            self.serial.write((message + "\n").encode(config.encoding))

            try:
                reply = self.serial.readline().decode(config.encoding).strip()
                # logger.debug(f"reply: {reply}")
                if expected_reply is not None and reply[0] != expected_reply:
                    raise ValueError(f"Unexpected reply from pico when sending message: {message}.\nReceived: {reply}")
                if reply_processing is not None:
                    try:
                        return reply_processing(reply)
                    except Exception as ee:
                        raise ValueError(
                            f"Error parsing a pico reply when sending message: {message}.\nReceived: {reply}"
                        )
                return reply

            except ValueError as e:
                if i < retries-1:
                    self.serial.flushInput()
                    continue
                if "reply" in locals():
                    print("reply:", reply)  # noqa
                if self.serial.in_waiting > 0:
                    print("in buffer:", self.serial.read_all())
                raise e

    # -------------------------
    # Lifecycle hooks
    # -------------------------

    def _activate(self):
        """
        Device activation: flush serial buffers and query Pico firmware version.

        Behavior
        --------
        - Flush input/output, send 'v', and store reply[1:] as `pico_version`.
        """
        self.serial.flushInput()
        self.serial.flushOutput()
        reply = self._write_and_read("v", "v")
        self.pico_version = reply[1:]

    def _deactivate(self):
        """
        Device deactivation: send 'r' (reset/ready) and close serial port.
        """
        self._write_and_read("r", "r")
        self.serial.close()

    def _stop(self):
        """
        Emergency stop: ensure LEDs are off and flush serial buffers.
        """
        self.write_leds_power(False)
        self.serial.flushOutput()
        self.serial.flushInput()

    # -------------------------
    # Core actions
    # -------------------------

    def write_measure(self, pins: tuple[int, int] = (0, 1), modes: tuple[int, int] = (1, 1)) -> np.ndarray:
        """
        Acquire a measurement from the Pico and parse it into a numeric array.

        Parameters
        ----------
        pins : tuple[int, int]
            Channels to read (paired with `modes`).
        modes : tuple[int, int]
            Per-channel mode codes recognized by the Pico firmware.

        Returns
        -------
        np.ndarray
            Parsed numeric data (dtype set by `self.dtype`).

        Notes
        -----
        - Ensures LED power is on for measurement (turns on if needed).
        - Issues the 's' command with a **concatenated pin-mode** payload.
        """
        if self.serial.in_waiting != 0:
            self.serial.flushInput()
        if not self.led_on:
            self.write_leds_power(on=True)

        return self._write_and_read(  # noqa
            message="s" + "".join(format_pin(pin, mode) for pin, mode in zip(pins, modes)),
            reply_processing=functools.partial(parse_measurement, dtype=self.dtype)
        )

    def write_gain(self, gain: int = 1):
        """
        Set ADC gain on the device.

        Parameters
        ----------
        gain : int
            One of {1, 2, 4, 8, 16}.
        """
        self._write_and_read(f"g{gain:02}", "g")

    def write_leds_power(self, on: bool = False):
        """
        Control LED power (inverted logic due to transistor switching).

        Parameters
        ----------
        on : bool
            True  → LED on  → send value 0
            False → LED off → send value 1

        Notes
        -----
        - Adds a short delay after power toggle to allow rails to stabilize.
        """
        self._write_and_read(f"d{int(not on)}", "d")
        time.sleep(0.1)  # to give time for power up
        self._led_on = on

    def write_offset_voltage(self, offset_voltage: int | float = 0):
        """
        Apply an offset voltage (V) to the signal path.

        Parameters
        ----------
        offset_voltage : int | float
            Expected range [0 .. 5] (device-specific).
        """
        self._write_and_read(f"o{offset_voltage:.06}", "o")

    def write_auto_offset_gain(self, gain: int = 16):
        """
        Auto-tune offset voltage and then set a desired final gain.

        Parameters
        ----------
        gain : int
            Final gain to set after offset calculation.

        Procedure
        ---------
        1) Set gain=1 to measure baseline.
        2) Turn LEDs on and gather `n` samples from both channels in raw mode.
        3) Convert averaged ADC counts to voltage, adjust by half-scale of target gain.
        4) Write offset voltage, write final gain, turn LEDs off.

        Notes
        -----
        - ADC conversion uses a 16-bit full scale and a 4.096 V reference in this code;
          verify against your hardware.
        """
        self.write_gain(1)

        n = 10
        self.write_leds_power(on=True)
        adc_value = np.zeros((n, 2), dtype=self.dtype)
        for i in range(n):
            adc_value[i, :] = self.write_measure((0, 1), (0, 0))

        # 16-bit ADC: (2**16 - 1) codes. Convert mean code to volts at gain=1.
        voltage = np.mean(np.mean(adc_value, axis=0)) / ((2 ** 16 - 1) / 2) * 4.096
        voltage += self.gains[gain] / 2
        logger.info(config.log_formatter(self, self.name, f"voltage_offset: {voltage}"))

        self.write_offset_voltage(voltage)
        self.write_gain(gain)
        self.write_leds_power(on=False)

    def write_next_slug(
        self,
        target_volume: Quantity,
        pump_names: list[str],
        timeout: float | int = None
    ) -> None | Slug:
        """
        Detect the next slug that reaches/exceeds `target_volume` while sampling.

        Parameters
        ----------
        target_volume : Quantity
            Minimum slug volume (e.g., 50 * Unit.uL) to trigger a return.
        pump_names : list[str]
            RabbitMQ equipment names of pumps whose flow rates are to be polled.
        timeout : float | int | None
            Optional time limit for detection (seconds). (Not enforced here; see SlugFinder.)

        Returns
        -------
        Slug | None
            The detected slug if found; otherwise None.
        """
        if self._slug_finder is None:
            self._slug_finder = SlugFinder(self, target_volume, pump_names, timeout)

        result = self._slug_finder.measure()
        if result is not None:  # stop slug finder and profile if a slug is found
            self._slug_finder = None
            self.continuous_event_handler = None

        return result


class SlugFinder:
    """
    Streaming slug detector built on a **CUSUM** change detector and pump telemetry.

    Responsibilities
    ----------------
    - Periodically **poll pumps** for flow rates via RabbitMQ actions and compute
      an **average flow** for velocity estimation.
    - Continuously **sample** the phase sensor (`write_measure`) and feed values
      into the **CUSUM** detector.
    - Create/close **Slug** objects on **up/down** events and return a slug when
      `slug.volume >= target_volume`.

    Notes
    -----
    - `check_flow_rate_rate` controls how frequently pump statuses are requested.
    - Incoming `RabbitMessageReply` messages are filtered by **message id** so
      unrelated messages in the shared queue are preserved.
    """
    check_flow_rate_rate = 2  # check every second

    def __init__(
        self,
        parent: PhaseSensor,
        target_volume: Quantity,
        pump_names: list[str],
        timeout: float | int = None
    ):
        self.parent = parent
        self.target_volume = target_volume
        self.pump_names = pump_names
        self.timeout = timeout

        self._flow_rate_sum: Quantity = 0 * Unit("ml/min")
        self._flow_rate_count = 0
        self._flow_rate_buffer = {k: None for k in self.pump_names}
        self._next_time = time.time() + self.check_flow_rate_rate
        self._message_ids: list[int] = []

        self.algorithm = CUSUM()
        self.slugs: list[Slug] = []

    @property
    def flow_rate(self) -> Quantity:
        """
        Return the averaged flow rate across recent pump replies.
        """
        if self._flow_rate_count is None:
            return 0 * Unit("ml/min")

        return self._flow_rate_sum / self._flow_rate_count

    @property
    def velocity(self) -> Quantity:
        """
        Convert bulk flow rate (mL/min) into linear velocity using tube cross-section.
        """
        return self.flow_rate / (np.pi * (self.parent.tube_diameter / 2) ** 2)

    def _update_flow_rate(self):
        """
        Send RabbitMQ messages to pumps asking for their **pump_state** (includes flow rate).

        Behavior
        --------
        - Throttled by `check_flow_rate_rate`.
        - Each message is registered with the watchdog and its id stored to match replies.
        """
        if time.time() < self._next_time:
            return

        for pump_name in self.pump_names:
            message = RabbitMessageAction(
                destination=pump_name,
                source=self.parent.name,
                action=SyringePump.read_pump_state.__name__
            )
            self.parent.rabbit.send(message)
            self.parent.watchdog.set_watchdog(message, self.check_flow_rate_rate)
            self._message_ids.append(message.id_)

    def _check_for_flow_rate_messages(self):
        """
        Consume pending Rabbit replies and update the flow-rate buffer.

        Notes
        -----
        - Non-matching messages are pushed back into the parent's queue to avoid
          interfering with other equipment handlers sharing the queue.
        - When a complete set of pump replies is gathered, the running average is updated.
        """
        if self.parent._message_queue.qsize() < 1:
            return

        for i in range(1, self.parent._message_queue.qsize() + 1):
            message: RabbitMessageReply = self.parent._message_queue.get()
            if message.id_ not in self._message_ids:
                self.parent._message_queue.put(message)
                continue
            if message.source not in self._flow_rate_buffer:
                raise ValueError("Coding error.")
            self._flow_rate_buffer[message.source] = message.value

        if all([v is not None for v in self._flow_rate_buffer.values()]):
            self._flow_rate_count += 1
            self._flow_rate_sum += sum([v for v in self._flow_rate_buffer.values()])

            # reset buffer to None
            for k in self._flow_rate_buffer:
                self._flow_rate_buffer[k] = None

    def measure(self) -> Slug | None:
        """
        Advance the slug detector by one sampling step.

        Behavior
        --------
        - Process any queued pump replies; send new pump queries if due.
        - Read a **single** new data point from the sensor (channel 0, mode 1).
        - Feed the point into **CUSUM**; on `States.up`, start a new `Slug`.
          On `States.down`, close the current slug and return it if volume >= target.

        Returns
        -------
        Slug | None
            A completed slug meeting the target volume, else None.
        """
        self._check_for_flow_rate_messages()
        self._update_flow_rate()

        new_data_point = self.parent.write_measure(pins=(0,), modes=(1,))
        event = self.algorithm.add_data(new_data_point)
        if event is CUSUM.States.up:
            self.slugs.append(Slug(time_start_1=time.time(), velocity=self.velocity))
        if event is CUSUM.States.down and self.slugs:
            self.slugs[-1].time_end_1 = time.time()
            if self.slugs[-1].volume > self.target_volume:
                return self.slugs[-1]  # slug found!