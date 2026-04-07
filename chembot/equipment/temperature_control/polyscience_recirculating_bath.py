"""PolyScience Circulating Bath"""
import logging
import pathlib
import time

import numpy as np
from serial import Serial
from unitpy import Quantity, Unit

from chembot.configuration import config
from chembot.equipment.temperature_control.base import TempControl
from chembot.utils.buffers.buffer_ping_pong import PingPongBuffer

logger = logging.getLogger(config.root_logger_name + ".polysciencebath")


class PolyScienceBath:
    """
    Low-level serial driver for the PolyScience circulating temperature bath.

    Purpose
    -------
    - Communicate directly with the PolyScience bath using its ASCII command set.
    - Expose read/write commands for:
        * temperature set point
        * actual temperature (internal & external probe)
        * pump speed
        * alarms
        * control mode (internal/external)
        * ON/OFF and RUN/STANDBY state
    - Provide strict acknowledgement checking using expected reply patterns.

    Notes
    -----
    - This class does **not** integrate with Chembot's Sensor/Equipment hierarchy.
      It is wrapped later by `PolyRecirculatingBath`, which plugs it into that system.
    """

    def __init__(
        self,
        comport: str,
        temp_limits: tuple[Quantity, Quantity] = (5 * Unit.degC, 60 * Unit.degC),
        encoding: str = "UTF8"
    ):
        # Open serial connection with 5s timeout
        self.serial = Serial(comport, timeout=5)
        self.encoding = encoding

        # Initialize bath and set communication configuration
        self._activate()

        self._temp_limits = None
        self.temp_limits = temp_limits

    def __enter__(self):
        """Context manager entry; enables `with PolyScienceBath(...) as bath:` usage."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """On context exit, cleanly shut down the bath."""
        self.deactivate()

    def _activate(self):
        """
        Initialize device state:
        - Flush I/O buffers
        - Turn unit ON
        - Disable echo
        - Set RUN/STANDBY status to known initial state
        """
        self.serial.flushInput()
        self.serial.flushOutput()
        self.write_on()
        self._write_echo(False)
        self.write_status(False)

    def deactivate(self):
        """Turn off the bath and close the serial connection."""
        self.write_off()
        self.serial.close()

    # -------------------------
    # Serial communication helpers
    # -------------------------

    def _read(self, expected_reply: str):
        """
        Read an exact reply from the instrument and validate it.

        Parameters
        ----------
        expected_reply : str
            Expected reply including carriage return.

        Raises
        ------
        RuntimeError
            If the reply does not match or if trailing bytes remain.
        """
        reply = self.serial.read(len(expected_reply))
        logger.debug("read: " + reply.decode())
        try:
            if reply.decode() != expected_reply or self.serial.in_waiting:
                raise RuntimeError("unexpected reply")
        except Exception as e:
            logger.error(f"reply:{reply}")
            if self.serial.in_waiting:
                logger.error(f"reply2:{self.serial.read(self.serial.in_waiting)}")
            raise e

    def _write(self, message: str):
        """
        Write an ASCII command to the bath, automatically appending CR.
        """
        logger.debug("write: " + message)
        self.serial.write((message + "\r").encode(self.encoding))

    def _write_echo(self, value: bool):
        """
        Enable or disable serial echo.

        value
        -----
        True  → enable echo
        False → disable echo (recommended)
        """
        self._write(f"SE{int(value)}")
        self._read("!\r")

    # -------------------------
    # Write commands
    # -------------------------

    def write_set_point(self, temperature: Quantity):
        """
        Set the bath's temperature set point.

        Parameters
        ----------
        temperature : Quantity (degC)
            Desired set point within configured temp limits.

        Raises
        ------
        ValueError
            If requested temperature is outside allowed limits.
        """
        if not (self.temp_limits[0] <= temperature <= self.temp_limits[1]):
            raise ValueError(
                f"Temperature outside temperature limits."
                f"\nGiven: {temperature}\nLimits: {self.temp_limits}"
            )

        # Convert to "xxx.xx" string format expected by device
        temp_degC = float(temperature.to(Unit.degC).v)
        temperature_string = f"{int(temp_degC):03}" + f"{temp_degC - int(temp_degC):0.2f}"[1:]

        self._write(f"SS{temperature_string}")
        self._read("!\r")

    def write_on(self):
        """Turn ON the bath output."""
        self._write(f"SO1")
        try:
            self._read("!\r")
        except Exception:
            # Some devices fail to ack immediately; ignore first failure
            pass

    def write_off(self):
        """Turn OFF the bath output."""
        self._write(f"SO0")
        # Optionally: self._read("!\r")

    def write_high_alarm(self, temperature: Quantity):
        """Set upper alarm limit."""
        temperature = int(temperature.to(Unit.degC).v)
        self._write(f"SH{temperature:03}")
        self._read("!\r")

    def write_low_alarm(self, temperature: Quantity):
        """Set lower alarm limit."""
        temperature = int(temperature.to(Unit.degC).v)
        self._write(f"SL{temperature:03}")
        self._read("!\r")

    def write_pump_speed(self, speed: int):
        """
        Set pump speed (5–100, increments of 5).
        """
        if not (5 <= speed <= 100):
            raise ValueError("Pump speed must be between 5-100.")

        # Normalize to nearest increment of 5
        speed = int(speed / 5) * 5

        self._write(f"SM{speed:03}")
        self._read("!\r")

    def write_status(self, value: bool):
        """
        Set RUN/standby status.

        value
        -----
        True  → RUN
        False → STANDBY
        """
        self._write(f"SW{int(value)}")
        self._read("!\r")

    def write_control(self, value: bool):
        """
        Choose control source.

        True  → external probe
        False → internal probe
        """
        self._write(f"SJ{int(value)}")
        self._read("!\r")

    # -------------------------
    # Read commands
    # -------------------------

    def read_set_point(self) -> Quantity:
        """Read temperature set point as Quantity(degC)."""
        self._write(f"RS")
        reply = self.serial.read_until("\r")
        try:
            return float(reply[:-1]) * Unit.degC
        except Exception as e:
            logger.error(f"reply:{reply}")
            if self.serial.in_waiting:
                logger.error(f"reply2:{self.serial.read(self.serial.in_waiting)}")
            raise e

    def read_units(self) -> str:
        """Return temperature units (“C” or “F”)."""
        self._write("RU")
        reply = self.serial.read_until("\r").decode(self.encoding)
        try:
            return reply[:-1]
        except Exception as e:
            logger.error(f"reply:{reply}")
            if self.serial.in_waiting:
                logger.error(f"reply2:{self.serial.read(self.serial.in_waiting)}")
            raise e

    def read_internal_temp(self) -> Quantity:
        """Read internal temperature probe."""
        self._write(f"RT")
        reply = self.serial.read_until("\r")
        try:
            return float(reply[:-1]) * Unit.degC
        except Exception as e:
            logger.error(f"reply:{reply}")
            if self.serial.in_waiting:
                logger.error(f"reply2:{self.serial.read(self.serial.in_waiting)}")
            raise e

    def read_external_temp(self) -> Quantity:
        """Read external temperature probe."""
        self._write(f"RR")
        reply = self.serial.read_until("\r")
        try:
            return float(reply[:-1]) * Unit.degC
        except Exception as e:
            logger.error(f"reply:{reply}")
            if self.serial.in_waiting:
                logger.error(f"reply2:{self.serial.read(self.serial.in_waiting)}")
            raise e

    def read_status(self) -> bool:
        """
        Read RUN/STANDBY status.

        Returns
        -------
        True  → RUN
        False → STANDBY
        """
        self._write(f"RO")
        reply = self.serial.read_until("\r")
        try:
            return bool(reply[:-1])
        except Exception as e:
            logger.error(f"reply:{reply}")
            if self.serial.in_waiting:
                logger.error(f"reply2:{self.serial.read(self.serial.in_waiting)}")
            raise e

    def read_high_alarm(self) -> Quantity:
        """Read high alarm temperature."""
        self._write(f"RH")
        reply = self.serial.read_until("\r")
        try:
            return float(reply[:-1]) * Unit.degC
        except Exception as e:
            logger.error(f"reply:{reply}")
            if self.serial.in_waiting:
                logger.error(f"reply2:{self.serial.read(self.serial.in_waiting)}")
            raise e

    def read_low_alarm(self) -> Quantity:
        """Read low alarm temperature."""
        self._write(f"RL")
        reply = self.serial.read_until("\r")
        try:
            return float(reply[:-1]) * Unit.degC
        except Exception as e:
            logger.error(f"reply:{reply}")
            if self.serial.in_waiting:
                logger.error(f"reply2:{self.serial.read(self.serial.in_waiting)}")
            raise e

    def read_pump_speed(self) -> int:
        """Read pump speed (5–100)."""
        self._write(f"RM")
        reply = self.serial.read_until("\r")
        try:
            return int(reply[:-1])
        except Exception as e:
            logger.error(f"reply:{reply}")
            if self.serial.in_waiting:
                logger.error(f"reply2:{self.serial.read(self.serial.in_waiting)}")
            raise e

    def read_alarm(self) -> bool:
        """Read bath fault flag (True = fault)."""
        self._write(f"RF")
        reply = self.serial.read_until("\r")
        try:
            return bool(reply[:-1])
        except Exception as e:
            logger.error(f"reply:{reply}")
            if self.serial.in_waiting:
                logger.error(f"reply2:{self.serial.read(self.serial.in_waiting)}")
            raise e

    def read_version(self) -> str:
        """Read device firmware version."""
        self._write(f"RF")
        reply = self.serial.read_until("\r")
        try:
            return reply[:-1]
        except Exception as e:
            logger.error(f"reply:{reply}")
            if self.serial.in_waiting:
                logger.error(f"reply2:{self.serial.read(self.serial.in_waiting)}")
            raise e


class PolyRecirculatingBath(TempControl):
    """
    High-level Chembot-integrated controller wrapping PolyScienceBath.

    Purpose
    -------
    - Provide a TempControl-compatible interface for the PolyScience bath.
    - Add continuous temperature logging via PingPongBuffer.
    - Integrate with Chembot Equipment lifecycle (_activate/_deactivate/_stop).
    """

    def __init__(
        self,
        name: str,
        comport: str,
        temp_limits: tuple[Quantity, Quantity] = (5 * Unit.degC, 60 * Unit.degC),
    ):
        # Wrap low-level bath driver
        self.bath = PolyScienceBath(comport, temp_limits)

        # Local temperature logger (auto-saves to CSV)
        self.buffer = PingPongBuffer(pathlib.Path("bath_temp.csv"), capacity=1000)

        # Rate limit for periodic temperature polling
        self._next_time = time.time()

        super().__init__(name=name)

    # -------------------------
    # Required TempControl API
    # -------------------------

    def write_set_point(self, temperature: float):
        """Set bath set point (°C)."""
        self.bath.write_set_point(temperature * Unit.degC)

    def read_set_point(self) -> Quantity:
        """Return quantity set point from bath."""
        return self.bath.read_set_point()

    def read_temperature(self) -> float:
        """Read current bath temperature (float)."""
        return self.bath.read_internal_temp().value

    # -------------------------
    # Lifecycle
    # -------------------------

    def _activate(self):
        """No additional activation required; handled by nested bath."""
        pass

    def _deactivate(self):
        """Save logs and shut down bath."""
        self.buffer.save_all()
        self.bath.deactivate()

    def _stop(self):
        """Soft halt: save data and stop bath circulation."""
        self.buffer.save_all()
        self.bath.write_status(False)

    # -------------------------
    # Periodic monitoring
    # -------------------------

    def _poll_status(self):
        """
        Append a periodic temperature reading to buffer every 30 seconds.

        Notes
        -----
        - This is a lightweight telemetry mechanism.
        - TODO: consider integrating with Chembot background workers.
        """
        time_ = time.time()
        if time_ > self._next_time:
            self._next_time = time_ + 30
            self.buffer.add_data(np.array((time_, self.read_temperature())))
            # TODO: provide a better async publisher for this data