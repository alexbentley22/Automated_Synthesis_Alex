from __future__ import annotations

import enum
import logging
import time
from datetime import timedelta

import serial
from unitpy import Quantity, Unit

from chembot.configuration import config
from chembot.equipment.pumps.syringe_pump import SyringePump, SyringePumpStatus
from chembot.equipment.pumps.syringes import Syringe
from chembot.communication.serial_ import Serial
from chembot.utils.unit_validation import validate_quantity

logger = logging.getLogger(config.root_logger_name + ".pump")


class HarvardPumpStatus(enum.Enum):
    """
    Single-character status flags returned by Harvard instruments.

    Notes
    -----
    These are the *leading* characters that can prefix many replies; they also
    appear as line terminators in some contexts. Mapping to the generic
    SyringePumpStatus is provided below in `map_status`.
    """
    STANDBY = ":"
    INFUSE = ">"      # '>' (HTML-escaped in this source view)
    WITHDRAW = "<"    # '<' (HTML-escaped in this source view)
    STALLED = "*"
    TARGET_REACHED = "T"


# Map Harvard-specific status flags to the generic SyringePumpStatus used in the framework
map_status = {
    HarvardPumpStatus.STANDBY: SyringePumpStatus.STANDBY,
    HarvardPumpStatus.INFUSE: SyringePumpStatus.INFUSE,
    HarvardPumpStatus.WITHDRAW: SyringePumpStatus.WITHDRAW,
    HarvardPumpStatus.STALLED: SyringePumpStatus.STALLED,
    HarvardPumpStatus.TARGET_REACHED: SyringePumpStatus.TARGET_REACHED
}


class CommandError(Exception):
    """
    Raised when a command is unrecognized, issued in the wrong mode, or blocked
    by the current pump state (as reported by the device).
    """


class ArgumentError(Exception):
    """
    Raised when a command argument is unrecognized or out of range. The offending
    argument is typically echoed in the pump's reply unless missing entirely.
    """

#######################################################################################################################
#######################################################################################################################


class HarvardPumpVersion:
    """
    Parsed fields from the 'version' command.

    Attributes
    ----------
    firmware : str | None
        Firmware version string.
    address : int | None
        Pump address (for multi-drop buses; often 0 for USB/standalone).
    serial_number : str | None
        Device serial number.
    """

    def __init__(self,
                 firmware: str = None,
                 address: int = None,
                 serial_number: str = None,
                 ):
        self.firmware = firmware
        self.address = address
        self.serial_number = serial_number

    @classmethod
    def parse_message(cls, message: str) -> HarvardPumpVersion:
        """
        Parse the long version string from the pump.

        Expected format (example)
        -------------------------
        '\\nFirmware:      v3.0.5\\r\\nPump address:  0\\r\\nSerial number: D401460\\r\\n:'

        Notes
        -----
        - The assignments below mirror the original code structure. Be aware there
          may be issues (e.g., multiple assignments to `firmware`); no logic has
          been altered here to preserve behavior.
        """
        # format: '\nFirmware:      v3.0.5\r\nPump address:  0\r\nSerial number: D401460\r\n:'
        message = message[:-3] \
            .replace("\n", "") \
            .replace("Firmware", "firmware") \
            .replace("Pump address", "address") \
            .replace("Serial number", "serial_number") \
            .replace(" ", "") \
            .split("\r")

        version = cls()
        version.firmware = message[0].split(":")[1]
        version.firmware = int(message[1].split(":")[1])     # NOTE: this overwrites firmware with an int (address?)
        version.firmware = message[2].split(":")[1]          # NOTE: overwritten again (likely intended serial_number)

        return version

#######################################################################################################################
#######################################################################################################################


class HarvardPumpStatusDirection(enum.Enum):
    """
    Direction flags used in the compact status word.
    """
    infuse = "i"
    withdraw = "w"


class HarvardPumpStatusMessage:
    """
    Structured status parsed from the pump's 'status' command response.

    Fields
    ------
    flow_rate : Quantity
        Flow rate reported in femtoliters per second (fL/s). Converted here with Unit().
    time_ : Quantity
        Elapsed time in milliseconds (ms) as reported by the pump.
    displaced_volume : Quantity
        Volume displaced in femtoliters (fL).
    motor_direction : HarvardPumpStatusDirection
        Current motor direction (infuse/withdraw).
    running : bool
        Whether the motor is actively running (True) or idle (False).
    limit_switch_hit_infuse / limit_switch_hit_withdraw : bool | None
        Whether the respective limit switch has been hit (True), not hit (False),
        or not present/unknown (None).
    stalled : bool
        True if pump is stalled.
    triggered : bool
        Trigger input flag (True when triggered).
    port_state : HarvardPumpStatusDirection
        Direction state of the port (i/w).
    target_reached : bool
        True if target volume/time reached.
    """

    directions = HarvardPumpStatusDirection

    def __init__(self,
                 flow_rate: Quantity = None,
                 time_: Quantity = None,
                 displaced_volume: Quantity = None,
                 motor_direction: HarvardPumpStatusDirection = None,
                 running: bool = None,
                 limit_switch_hit_infuse: bool | None = None,
                 limit_switch_hit_withdraw: bool | None = None,
                 stalled: bool = None,
                 triggered: bool = None,
                 port_state: HarvardPumpStatusDirection = None,
                 target_reached: bool = None
                 ):
        self.flow_rate = flow_rate
        self.time_ = time_
        self.displaced_volume = displaced_volume
        self.motor_direction = motor_direction
        self.running = running
        self.limit_switch_hit_infuse = limit_switch_hit_infuse
        self.limit_switch_hit_withdraw = limit_switch_hit_withdraw
        self.stalled = stalled
        self.triggered = triggered
        self.port_state = port_state
        self.target_reached = target_reached

    @classmethod
    def parse_message(cls, message: str) -> HarvardPumpStatusMessage:
        """
        Parse the compact 'status' response.

        Expected format (example)
        -------------------------
        '\\n0 0 0 w..TI.\\r\\n:'

        Parsing logic
        -------------
        - First three integers: flow_rate (fL/s), time (ms), displaced_volume (fL).
        - Fourth token is a compact flag field (6 chars):
            [0] motor direction + running (case indicates motion)
            [1] limit switch (i/I or w/W), '.' → none
            [2] 'S' → stalled else '.'
            [3] 'T' → triggered else '.'
            [4] port state (i/I or w/W)
            [5] 'T' → target reached else '.'
        """
        # parse reply
        # format: '\n0 0 0 w..TI.\r\n:'
        message = message \
            .replace("\n", "") \
            .split(" ")

        status = cls()
        # integer terms
        status.flow_rate = int(message[0]) * Unit("fL/s")  # Yes, it is femtoliters per second
        status.time_ = int(message[1]) * Unit("ms")
        status.displaced_volume = int(message[2]) * Unit("fL")  # Yes, it is femtoliters

        flag_field = message[3]
        # first term: direction + running state via case
        if flag_field[0] == "i":
            status.motor_direction = HarvardPumpStatusDirection.infuse
            status.running = False
        elif flag_field[0] == "I":
            status.motor_direction = HarvardPumpStatusDirection.infuse
            status.running = True
        elif flag_field[0] == "w":
            status.motor_direction = HarvardPumpStatusDirection.withdraw
            status.running = False
        else:  # "W"
            status.motor_direction = HarvardPumpStatusDirection.withdraw
            status.running = True

        # second term: limit switch indicator (directional)
        if flag_field[1] == "i" or flag_field[1] == "I":
            status.limit_switch_hit_infuse = True
        elif flag_field[1] == "w" or flag_field[1] == "W":
            status.limit_switch_hit_withdraw = True
        # '.' → leave both as None

        # third term: stall flag
        if flag_field[2] == "S":
            status.stalled = True
        else:  # '.'
            status.stalled = False

        # forth term: trigger input
        if flag_field[3] == "T":
            status.triggered = True
        else:  # '.'
            status.triggered = False

        # fifth term: port state (direction)
        if flag_field[4] == "i" or flag_field[4] == "I":
            status.port_state = HarvardPumpStatusDirection.infuse
        else:  # 'w' or 'W'
            status.port_state = HarvardPumpStatusDirection.withdraw

        # sixth term: target reached
        if flag_field[5] == "T":
            status.target_reached = True
        else:  # '.'
            status.target_reached = False

        return status

#######################################################################################################################
#######################################################################################################################


class RampFlowRate:
    """
    Definition of a linear flow-rate ramp: start → end over a specified time.

    Parameters
    ----------
    flow_rate_start : Quantity
        Start flow rate (validated for syringe flow rate dimensionality).
    flow_rate_end : Quantity
        End flow rate (validated).
    time_ramp : timedelta
        Duration of the ramp.

    Methods
    -------
    as_string() -> str
        Stringified representation expected by the pump command set.
    """

    def __init__(self,
                 flow_rate_start: Quantity = None,
                 flow_rate_end: Quantity = None,
                 time_ramp: timedelta = None,
                 ):
        self._flow_rate_start = None
        self._flow_rate_end = None

        self.flow_rate_start = flow_rate_start
        self.flow_rate_end = flow_rate_end
        self.time_ramp = time_ramp

    @property
    def flow_rate_start(self) -> Quantity:
        return self._flow_rate_start

    @flow_rate_start.setter
    def flow_rate_start(self, flow_rate: Quantity):
        if flow_rate is None:
            return
        validate_quantity(flow_rate, Syringe.flow_rate_dimensionality, 'Ramp.flow_rate_start', True)
        self._flow_rate_start = flow_rate

    @property
    def flow_rate_end(self) -> Quantity:
        return self._flow_rate_end

    @flow_rate_end.setter
    def flow_rate_end(self, flow_rate: Quantity):
        if flow_rate is None:
            return
        validate_quantity(flow_rate, Syringe.flow_rate_dimensionality, 'Ramp.flow_rate_end', True)
        self._flow_rate_end = flow_rate

    def as_string(self) -> str:
        """
        Compose the ramp parameters as a single string for the Harvard pump.

        Format
        ------
        "<start_value> <start_unit> <end_value> <end_unit> <seconds>"

        Notes
        -----
        Units are normalized to ml/min, ul/min, or nl/min for cleaner strings.
        """
        flow_rate_start = set_flow_rate_range(self.flow_rate_start)
        flow_rate_end = set_flow_rate_range(self.flow_rate_end)
        return f"{flow_rate_start.v} {flow_rate_start.unit.abbr} {flow_rate_end.v} {flow_rate_end.unit.abbr} " \
               f"{self.time_ramp.total_seconds()}"

#######################################################################################################################
#######################################################################################################################


def set_flow_rate_range(flow_rate: Quantity) -> Quantity:
    """
    Normalize flow rate into a convenient unit bucket for command strings.

    Heuristic
    ---------
    - > 0.1 ml/min  → ml/min
    - > 0.1 ul/min  → ul/min
    - else          → nl/min
    """
    # change units for correct string formatting
    if flow_rate > 0.1 * Unit("ml/min"):
        return flow_rate.to("ml/min")
    elif flow_rate > 0.1 * Unit("ul/min"):
        return flow_rate.to("ul/min")
    else:
        return flow_rate.to("nl/min")


def set_volume_range(volume: Quantity) -> Quantity:
    """
    Normalize volume into a convenient unit bucket for command strings.

    Heuristic
    ---------
    - > 0.1 ml  → ml
    - > 0.1 ul  → ul
    - else      → nl
    """
    # change units for correct string formatting
    if volume > 0.1 * Unit("ml"):
        return volume.to("ml")
    elif volume > 0.1 * Unit("ul"):
        return volume.to("ul")
    else:
        return volume.to("nl")


def process_time(time_str: str) -> timedelta:
    """
    Parse HH:MM:SS into a timedelta.
    """
    h, m, s = time_str.split(':')
    return timedelta(hours=int(h), minutes=int(m), seconds=int(s))


class SyringePumpHarvard(SyringePump):
    """
    Harvard syringe pump driver (USB/serial; assumes echo is off).

    Behavior
    --------
    - The pump **sends a reply** when **target is reached**.
    - It **does NOT** send a reply when **stalled** (polling or buffer checks required).

    Integration
    -----------
    - Subclass of `SyringePump` with Harvard-specific command strings and parsing.
    - Manages a direct pySerial link (not via RabbitMQ) because the pump can
      emit unsolicited messages (e.g., status) without prompt.

    Attributes
    ----------
    ramp_object : type
        Ramp class used for ramp commands (`RampFlowRate`).
    poll_gap : int
        Time (seconds) between background polling while running.
    """

    ramp_object = RampFlowRate
    poll_gap = 5  # sec

    def __init__(self,
                 name: str,
                 syringe: Syringe,
                 port: str,
                 max_pull: Quantity = None,
                 # control_method: PumpControlMethod = PumpControlMethod.flow_rate,
                 ):
        super().__init__(name, syringe, max_pull)

        # Serial is created directly here (USB device is 1:1). The pump can push
        # messages without prompts so we keep a dedicated handle.
        Serial.available_port(port)
        self.serial = serial.Serial(port=port, timeout=0.4)
        self.serial.flushInput()
        self.serial.flushOutput()

        self._next_poll_time = 0

    def _check_pump_reply(self, message: str) -> str:
        """
        Inspect the pump reply for status prefixes and update state accordingly.

        Logic
        -----
        - If 'T' is present at message[0], set TARGET_REACHED and return remainder.
        - Otherwise map the first char to known status (':', '*', '>', '<') and
          set the generic SyringePumpStatus. Unrecognized → error.

        Notes
        -----
        - The reply parsing here follows the original code flow. If 'STALLED'
          appears immediately after 'T', an extra 'stop' is issued.
        """
        # check for target reached  'T:' or 'T*'
        if "T" in message[0]:
            self.pump_state.state = SyringePumpStatus.TARGET_REACHED
            self.state = self.states.STANDBY
            if message[1] == HarvardPumpStatus.STALLED.value:
                self._send_and_receive_message("stop")
            return message[2:]

        # check general status ':', '*', '>', '<'
        if message[0] == HarvardPumpStatus.STALLED.value:
            self.state = self.states.STANDBY
            self._send_and_receive_message("stop")
        status = message[0]
        for option in HarvardPumpStatus:
            if status == option.value:
                self.pump_state.state = map_status[option]
                break
        else:
            logger.error(f"message:{message}")
            raise ValueError("Unrecognized status from Harvard Pump reply.")

    def _send_and_receive_message(self,
                                  prompt: str,
                                  time_out: float = 0.2,
                                  retries: int = 3
                                  ) -> str:
        """
        Send a command with '@' (to disable GUI updates) and read the reply.

        Parameters
        ----------
        prompt : str
            Command to send (without terminator).
        time_out : float
            Serial read timeout per attempt.
        retries : int
            Number of retries on transient errors.

        Returns
        -------
        str
            Raw reply string from the pump.

        Notes
        -----
        - '@' prefix is used per vendor guidance to speed communications.
        - On failure, flush input (except last attempt) and retry.
        """
        logger.debug(f"{self.name} | send: {prompt}")
        for i in range(retries):
            # '@' turns off GUI updates for faster communication rates
            self.serial.write(("@" + prompt + "\r").encode(config.encoding))

            try:
                return self._read(time_out)
            except Exception as e:
                if i < retries-1:
                    self.serial.flushInput()
                    continue
                raise e

    def _read(self, time_out: float | int = 0.2, retries: int = 3) -> str:
        """
        Read a two-line reply from the pump (first often '\\n', second contains data).

        Parameters
        ----------
        time_out : float | int
            Serial timeout for this read sequence.
        retries : int
            Retries on transient issues.

        Notes
        -----
        Message format: <lf><payload>
          - first read_until commonly returns "\\n"
          - second read contains the data

        Raises
        ------
        ArgumentError, CommandError
            If the reply contains device-reported argument/command errors.
        """
        self.serial.timeout = time_out
        for i in range(retries):
            try:
                reply = self.serial.read_until().decode(config.encoding)
                if reply == "\n" or reply == "\r\n":
                    reply = self.serial.read_until().decode(config.encoding)
                logger.debug(f"{self.name} | reply: " + reply.replace("\n", r"\n").replace("\r", r"\r"))
                if "Argument error" in reply:
                    raise ArgumentError(reply)
                if "Command error" in reply:
                    raise CommandError(reply)
                return reply

            except Exception as e:
                if i < retries-1 and (e is not ArgumentError or e is not CommandError):
                    continue
                if "reply" in locals():
                    print("reply:", reply)  # noqa
                if self.serial.in_waiting > 0:
                    print("in buffer:", self.serial.read_all())
                raise e

    def _activate(self):
        """
        Equipment activation: configure syringe diameter/force, speed up comms.

        Steps
        -----
        - 'NVRAM off' to reduce writes and increase command throughput.
        - Set syringe diameter and force (from `self.syringe`).
        """
        # set syringe settings
        self._send_and_receive_message("NVRAM off")  # turn off writes of rate to memory -> faster communication
        self._write_diameter(self.syringe.diameter)
        # self.write_empty()
        self.write_force(self.syringe.force)
        super()._activate()

    def _deactivate(self):
        """
        Equipment deactivation: stop the pump and close the serial port.
        """
        self._stop()
        self.serial.close()

    def _poll_status(self):
        """
        Periodic background poll to detect completion or stall conditions.

        Behavior
        --------
        - If bytes are waiting, read and log "finished addition or stalled."
        - If stalled while RUNNING and not near zero volume, escalate error and stop.
        - While RUNNING, poll full status every `poll_gap` seconds.
        """
        if self.serial.in_waiting:
            self._read()
            logger.info(f"{self.name} | Pump finished addition or stalled.")
            if self.pump_state.state is SyringePumpStatus.STALLED and self.state is self.states.RUNNING:
                if not self.pump_state.volume_in_syringe.is_close(0 * Unit.ml, abs_tol=0.01 * Unit.ml):
                    # ignore stall if it's close to zero volume in syringe
                    logger.error(config.log_formatter(self, self.name, "Error stalled detected!!!"))
                self.write_stop()

        if self.state is self.states.RUNNING and time.time() > self._next_poll_time:
            self.read_pump_status()
            self._next_poll_time = time.time() + self.poll_gap

    ## actions ################################################################################################# noqa
    def _stop(self):
        """
        Stop the pump and update internal state.
        """
        reply = self._send_and_receive_message("stop")
        self._check_pump_reply(reply)
        self.pump_state.flow_rate = 0 * Unit("ml/min")

    def _write_run_infuse(self):
        """
        Run (infuse).
        """
        reply = self._send_and_receive_message('irun')
        self._check_pump_reply(reply)
        self.state = self.states.RUNNING
        self.pump_state.state = self.pump_states.INFUSE
        self.pump_state.running_time = 0 * Unit.s
        self.pump_state.volume_displace = 0 * self.syringe.volume.unit

    def _write_run_withdraw(self):
        """
        Run (withdraw).
        """
        reply = self._send_and_receive_message(f'wrun')
        self._check_pump_reply(reply)
        self.state = self.states.RUNNING
        self.pump_state.state = self.pump_states.WITHDRAW
        self.pump_state.running_time = 0 * Unit.s
        self.pump_state.volume_displace = 0 * self.syringe.volume.unit

    def _write_run_withdraw2(self):
        """
        Alternate run (withdraw) command path.

        Notes
        -----
        - Uses 'run' (no direction prefix). If state not withdraw, flips direction.
        """
        reply = self._send_and_receive_message(f'run')
        self._check_pump_reply(reply)
        if self.pump_state.state != HarvardPumpStatus.WITHDRAW:
            self._flip_direction()

        self.state = self.states.RUNNING
        self.pump_state.state = self.pump_states.WITHDRAW
        self.pump_state.running_time = 0 * Unit.s
        self.pump_state.volume_displace = 0 * self.syringe.volume.unit

    def _flip_direction(self):
        """
        Reverse direction ('rrun')—device-specific behavior.
        """
        reply = self._send_and_receive_message(f'rrun')

    def write_infuse(self, volume: Quantity, flow_rate: Quantity, ignore_syringe_error: bool = False):
        """
        Infuse a target volume at a specified flow rate.

        Parameters
        ----------
        volume : Quantity
            Target volume to infuse.
        flow_rate : Quantity
            Flow rate to use.
        ignore_syringe_error : bool
            If True, do not raise error if stall occurs (caller handles it).
        """
        # validation of inputs
        validate_quantity(volume, Syringe.volume_dimensionality, "volume", True)
        validate_quantity(flow_rate, Syringe.flow_rate_dimensionality, "flow_rate", True)
        # If pull-limit checks are needed, enable the commented logic below.

        # setup pump
        self._write_infuse_volume_clear()
        self._write_infuse_time_clear()
        self._write_target_time_clear()
        self._write_target_volume(volume)
        self.write_infusion_rate(flow_rate)
        self.write_force(100)  # TODO: improve; turn down after some time
        # self._write_target_time(self.compute_run_time(volume, flow_rate).to_timedelta())

        # run
        self._write_run_infuse()

        # update status
        self.pump_state.flow_rate = flow_rate
        self.pump_state.target_volume = volume
        self.pump_state.end_time = self.compute_run_time(volume, flow_rate)

    def write_withdraw(self, volume: Quantity, flow_rate: Quantity):
        """
        Withdraw a target volume at a specified flow rate.

        Parameters
        ----------
        volume : Quantity
            Target volume to withdraw.
        flow_rate : Quantity
            Flow rate to use.
        """
        # validation of inputs
        validate_quantity(volume, Syringe.volume_dimensionality, "volume", True)
        validate_quantity(flow_rate, Syringe.flow_rate_dimensionality, "flow_rate", True)

        # setup pump
        self._write_withdraw_volume_clear()
        self._write_withdrawn_time_clear()
        self._write_target_time_clear()
        self._write_target_volume(volume)
        self.write_withdraw_rate(flow_rate)
        self.write_force(100)
        # self._write_target_time(self.compute_run_time(volume, flow_rate).to_timedelta())

        # run
        self._write_run_withdraw()  #########################

        # update status
        self.pump_state.flow_rate = flow_rate
        self.pump_state.target_volume = volume
        self.pump_state.end_time = self.compute_run_time(volume, flow_rate)

    def write_empty(self, flow_rate: Quantity = None):
        """
        Empty the syringe by infusing its full volume until stall (with timeout).

        Behavior
        --------
        - Infuse the syringe's full volume at given (or default) flow rate.
        - Poll status until STALLED (or timeout/lack of stall → raise).
        - Stop to silence any tone and set volume_in_syringe to zero.
        """
        if flow_rate is None:
            flow_rate = self.syringe.default_flow_rate
        self.write_infuse(self.syringe.volume, flow_rate, ignore_syringe_error=True)

        # run till it stalls (with safe timeout)
        self.pump_state.volume_in_syringe = 0 * self.syringe.volume.unit
        time_out = (self.syringe.volume / self.syringe.default_flow_rate).to("s").value + 10  # seconds
        time_stop = time.time() + time_out
        self.write_force(50)
        while time.time() < time_stop:
            self.read_pump_status()
            if self.pump_state.state is SyringePumpStatus.STALLED:
                break
            time.sleep(0.1)

        if self.pump_state.state is not SyringePumpStatus.STALLED:
            raise ValueError("Pump not successful at emptying.")

        self.pump_state.volume_in_syringe = 0 * self.syringe.volume.unit
        self._stop()  # to stop tone

    def write_refill(self, flow_rate: Quantity = None):
        """
        Refill the syringe up to its maximum volume.
        """
        if flow_rate is None:
            flow_rate = self.syringe.default_flow_rate

        volume = self.syringe.volume - self.pump_state.volume_in_syringe
        self.write_withdraw(volume, flow_rate)

    # def _write_run(self):
    #     _ = self._send_and_receive_message('run')

    ## settings ################################################################################################# noqa
    # def _write_echo_off(self):
    #     """ sets echo state to off """
    #     _ = self._send_and_receive_message('echo off')

    def read_version(self) -> str:
        """
        Display the short version string.

        Returns
        -------
        str
            Example: 'PHD ULTRA 3.0.5'
        """
        reply = self._send_and_receive_message('ver')

        # parse reply
        # format b'\nPHD ULTRA 3.0.5\r\n:'
        reply = reply[:-3].replace("\n", "").replace("\r", "")

        return reply

    def read_version_long(self) -> HarvardPumpVersion:
        """
        Display the full version string and parse it.
        """
        reply = self._send_and_receive_message('version')
        return HarvardPumpVersion.parse_message(reply)

    def read_pump_status(self) -> HarvardPumpStatusMessage:
        """
        Display the raw status for a controlling computer and parse it.

        Behavior
        --------
        - Issues 'status', then reads an additional line. If the second line
          starts with a digit, the device returned lines in the older order,
          so they are swapped before parsing.
        - Calls `_check_pump_reply` with the non-numeric line to update
          state (e.g., T/':'/'*' flags).
        """
        try:
            reply = self._send_and_receive_message('status')
            reply2 = self._read()
            if reply2[0].isdigit():  # old pumps flip order
                reply, reply2 = reply2, reply
            self._check_pump_reply(reply2)

            status = HarvardPumpStatusMessage.parse_message(reply)
            return status
        except Exception:
            logger.warning("invalid status received.")
        # Optionally update cached pump_state fields here from parsed status.

    def read_force(self) -> int:
        """
        Display the infusion force level (%) as an integer.
        """
        reply = self._send_and_receive_message('force')

        # parse reply
        # format: '\n100%\r\n:'
        reply = reply[:-2] \
            .replace("\n", "")\
            .replace("%", "")
        return int(reply)

    def write_force(self, force: int):
        """
        Set the infusion force level in percent.

        Parameters
        ----------
        force : int
            Percent force. Expected range: [30 .. 100].

        Raises
        ------
        ValueError
            If outside allowed range.
        """
        # input validation
        force = int(force)
        if not (1 <= force <= 100):
            raise ValueError("force outside range [30, 100]")

        _ = self._send_and_receive_message(f'force {force:03}')

    def _read_diameter(self) -> Quantity:
        """
        Display the syringe diameter as a Quantity (e.g., '9.5250 mm').
        """
        reply = self._send_and_receive_message(f'diameter')

        # parse reply
        # format: '\n9.5250 mm\r\n:'
        reply = reply[:-2] \
            .replace("\n", "")\
            .replace("\r", "")

        return Quantity(reply)

    def write_syringe(self, syringe: Syringe):
        """
        Configure syringe parameters on the pump, and call base to update model.
        """
        self._write_diameter(syringe.diameter)
        super().write_syringe(syringe)

    def _write_diameter(self, diameter: Quantity):
        """
        Write syringe inner diameter in mm (bounded by device max).
        """
        # input validation
        diameter = diameter.to("mm")
        if diameter.value > 45:
            raise ValueError("diameter outside range [0, 45 mm]")

        reply = self._send_and_receive_message(f'diameter {diameter.v:2.4f}')
        self._check_pump_reply(reply)

    # def _read_gang(self) -> int:
    #     """ Displays the syringe count """
    #     reply = self._send_and_receive_message(f'gang')
    #
    #     # parse reply
    #     # format: '\n1 syringes\r\n:'
    #     return int(reply[1])
    #
    # def _write_gang(self, gang: int):
    #     """ Set syringe count -- effect on rate calc varies by device model """
    #     if 0 < gang < 3:
    #         raise ValueError("diameter outside range [0, 2]")
    #     _ = self._send_and_receive_message(f'gang {gang}')

    ## flow rate ################################################################################################# noqa
    def _read_max_flow_rate(self) -> Quantity:
        """
        Get the max flow rate accepted by the pump for the current syringe.
        """
        reply = self._send_and_receive_message(f'irate lim')

        # parse reply
        # format: '\n14.7792 nl/min to 15.3477 ml/min\r\n:'
        reply = reply.replace("\n", "").replace("\r", "")
        index = reply.index("o")
        return Quantity(reply[index+2:])

    def _read_min_flow_rate(self) -> Quantity:
        """
        Get the min flow rate accepted by the pump for the current syringe.
        """
        reply = self._send_and_receive_message(f'irate lim')

        # parse reply
        # format: '\n14.7792 nl/min to 15.3477 ml/min\r\n:'
        reply = reply.replace("\n", "")
        index = reply.index("t")
        return Quantity(reply[:index])

    def read_infusion_rate(self) -> Quantity:
        """
        Query the current infusion rate as a Quantity.
        """
        reply = self._send_and_receive_message(f'irate')

        # parse reply
        # format: '\n15.3477 ml/min\r\n:'
        reply = reply.replace("\n", "").replace("\r", "")
        return Quantity(reply)

    def write_infusion_rate(self, flow_rate: Quantity):
        """
        Set the infusion rate (unit-normalized to a compact bucket).
        """
        flow_rate = set_flow_rate_range(flow_rate)
        _ = self._send_and_receive_message(f'irate {flow_rate.v:2.4f} {flow_rate.unit.abbr}')
        self.pump_state.flow_rate = flow_rate

    def read_withdraw_rate(self) -> Quantity:
        """
        Query the current withdraw rate as a Quantity.
        """
        reply = self._send_and_receive_message(f'wrate')

        # parse reply
        # format: '\n15.3477 ml/min\r\n:'
        reply = reply.replace("\n", "").replace("\r", "")
        return Quantity(reply)

    def write_withdraw_rate(self, flow_rate: Quantity):
        """
        Set the withdraw rate (unit-normalized).
        """
        flow_rate = set_flow_rate_range(flow_rate)
        _ = self._send_and_receive_message(f'wrate {flow_rate.v:2.4f} {flow_rate.unit.abbr}')
        self.pump_state.flow_rate = flow_rate

    ## volume ################################################################################################### noqa
    def _read_infuse_volume(self) -> Quantity:
        """
        Read infused volume.
        """
        reply = self._send_and_receive_message(f'ivolume')

        # parse reply
        # format: b'\n0 ul\r\n:'
        reply = reply.replace("\n", "").replace("\r", "")
        return Quantity(reply)

    def _read_withdraw_volume(self) -> Quantity:
        """
        Read withdrawn volume.
        """
        reply = self._send_and_receive_message(f'wvolume')

        # parse reply
        # format: b'\n0 ul\r\n:'
        reply = reply.replace("\n", "").replace("\r", "")
        return Quantity(reply)

    def _read_target_volume(self) -> Quantity | None:
        """
        Read target volume (if any).
        """
        reply = self._send_and_receive_message(f'tvolume')

        # parse reply
        # format: b'\n0 ul\r\n:'
        if "Target" in reply:  # 'target volume not set' returns None
            return None

        reply = reply.replace("\n", "").replace("\r", "")
        return Quantity(reply)

    def _write_target_volume(self, volume: Quantity):
        """
        Set target volume.
        """
        volume = set_volume_range(volume)
        _ = self._send_and_receive_message(f'tvolume {volume.v:2.4f} {volume.unit.abbr}')

    def _write_target_volume_clear(self):
        """
        Clear target volume.
        """
        _ = self._send_and_receive_message(f'ctvolume')

    def _write_infuse_volume_clear(self):
        """
        Clear infused volume accumulator.
        """
        _ = self._send_and_receive_message(f'civolume')

    def _write_withdraw_volume_clear(self):
        """
        Clear withdrawn volume accumulator.
        """
        _ = self._send_and_receive_message(f'cwvolume')

    ## time #################################################################################################### noqa
    def _read_infuse_time(self) -> timedelta:
        """
        Read infusion time (seconds or HH:MM:SS depending on device state).
        """
        reply = self._send_and_receive_message(f'itime')

        # parse reply
        # format: b'\n0 seconds\r\n:'
        reply = reply.replace("\n", "").replace("\r", "").replace(" ", "")
        if "seconds" in reply:
            reply = reply.replace("seconds", "")
            return timedelta(seconds=int(reply))

        # format: '\n00:01:40\r\n:'
        return process_time(reply)

    def _read_withdraw_time(self) -> timedelta:
        """
        Read withdraw time (seconds or HH:MM:SS).
        """
        reply = self._send_and_receive_message(f'wtime')

        # parse reply
        reply = reply.replace("\n", "").replace("\r", "").replace(" ", "")
        if "seconds" in reply:
            reply = reply.replace("seconds", "")
            return timedelta(seconds=int(reply))

        # format: '\n00:01:40\r\n:'
        return process_time(reply)

    def _read_target_time(self) -> timedelta | None:
        """
        Read target time if set; else None.
        """
        reply = self._send_and_receive_message(f'ttime')

        # parse reply
        # format: '\n0 ul\r\n:'
        if "Target" in reply:  # 'target volume not set' returns None
            return None

        reply = reply.replace("\n", "").replace("\r", "").replace(" ", "")
        if "seconds" in reply:
            reply = reply.replace("seconds", "")
            return timedelta(seconds=int(reply))

        # format: '\n00:01:40\r\n:'
        return process_time(reply)

    def _write_target_time(self, time_: timedelta):
        """
        Set the target time in HH:MM:SS.
        """
        sec = time_.total_seconds()
        hours = int(sec // 3600)
        sec -= (hours * 3600)
        minutes = int(sec // 60)
        sec -= (minutes * 60)
        sec = int(sec)
        _ = self._send_and_receive_message(f'ttime {hours:02}:{minutes:02}:{sec:02}')

    def _write_target_time_clear(self):
        """
        Clear target time.
        """
        _ = self._send_and_receive_message(f'cttime')

    def _write_withdrawn_time_clear(self):
        """
        Clear withdrawn time accumulator.
        """
        _ = self._send_and_receive_message(f'cwtime')

    def _write_infuse_time_clear(self):
        """
        Clear infused time accumulator.
        """
        _ = self._send_and_receive_message(f'citime')

    def _write_target_clear(self):
        """
        Clear both target time and target volume.
        """
        self._write_target_time_clear()
        self._write_target_volume_clear()

    ## ramp #################################################################################################### noqa
    def _read_infuse_ramp(self) -> RampFlowRate | None:
        """
        Read the configured infusion ramp (if any) and parse it.
        """
        reply = self._send_and_receive_message(f'iramp')

        # parse reply
        # format: '\n15.3477 ml/min to 15.3477 ml/min in 80 seconds\r\n:'
        if "Ramp" in reply:  # RAMP not set up.
            return None

        reply = reply.replace("\n", "")
        reply = reply.strip(" to ")
        flow_rate_start = Quantity(reply[0])
        reply = reply[2].split(" in ")
        flow_rate_end = Quantity(reply[0])
        reply = reply[1].replace(" seconds\r", "")
        time_ = timedelta(int(reply))

        return RampFlowRate(flow_rate_start, flow_rate_end, time_)

    def write_infuse_ramp(self, ramp: RampFlowRate):
        """
        Configure and run an infusion ramp.
        """
        _ = self._send_and_receive_message(f'iramp {ramp.as_string()}')

        # run
        self._write_run_infuse()

    def _read_withdraw_ramp(self) -> RampFlowRate | None:
        """
        Read the configured withdraw ramp (if any).
        """
        reply = self._send_and_receive_message(f'wramp')

        # parse reply
        # format: '\n15.3477 ml/min to 15.3477 ml/min in 80 seconds\r\n:'
        if "Ramp" in reply:  # RAMP not set up.
            return None

        reply = reply.replace("\n", "")
        reply = reply.strip(" to ")
        flow_rate_start = Quantity(reply[0])
        reply = reply[2].split(" in ")
        flow_rate_end = Quantity(reply[0])
        reply = reply[1].replace(" seconds\r", "")
        time_ = timedelta(int(reply))

        return RampFlowRate(flow_rate_start, flow_rate_end, time_)

    def write_withdraw_ramp(self, ramp: RampFlowRate):
        """
        Configure and run a withdraw ramp.
        """
        _ = self._send_and_receive_message(f'wramp {ramp.as_string()}')

        # run
        self._write_run_withdraw()