import enum
import math
import time
import logging

from equipment.pumps.base import SyringePump
from equipment.continuous_event_handler import PumpFlowProfile
from chembot.communication.serial_ import Serial
from chembot.errors import EquipmentError


logger = logging.getLogger("ChemBot.pump")


class PumpHarvardStates(enum.Enum):
    """
    One-character terminal status indicators used in Harvard replies.

    Notes
    -----
    - These values sometimes appear HTML-escaped in source ('&gt;' / '&lt;'),
      but the semantic meaning is:
        ':'  → stopped/standby
        '>'  → running forward (infuse)
        '<'  → running backward (withdraw)
        '*'  → stalled / target reached (context dependent)
    """
    stopped = ":"
    running_forward = "&gt;"
    running_backward = "&lt;"
    stalled = "*"


def remove_crud(string: str) -> str:
    """
    Normalize numeric strings by trimming cosmetic characters.

    Behavior
    --------
    - If a decimal point is present, strip trailing zeros after it.
    - Strip leading zeros and spaces.
    - Strip trailing spaces and solitary decimal points.

    Parameters
    ----------
    string : str
        Raw device numeric string.

    Returns
    -------
    str
        Cleaned numeric string suitable for comparisons/echo checks.
    """
    if "." in string:
        string = string.rstrip('0')

    string = string.lstrip('0 ')
    string = string.rstrip(' .')

    return string


def _format_diameter(pump: SyringePump, diameter: float) -> str:
    """
    Format a diameter value for the device (2 decimal places max).

    Notes
    -----
    - Pump firmware ignores precision beyond 2 dp; this truncates/rounds to 2 dp
      and logs a warning if truncation occurred.
    """
    # SyringePump only considers 2 d.p. - anymore are ignored
    diameter_str = str(round(diameter, 2))

    if diameter != float(diameter_str):
        logger.warning(f'{pump.class_name} diameter truncated to {diameter_str} mm')

    return diameter_str


def _format_flow_rate(pump: SyringePump, flow_rate: int | float) -> str:
    """
    Format flow rate for the device (field width up to ~5 chars).

    Notes
    -----
    - Harvard Pump 11 requires a compact string like "XXXX." or "X.XXX".
      This trims the string if needed and normalizes zeros/spaces.
    """
    flow_rate = str(flow_rate)

    if len(flow_rate) > 5:
        flow_rate = flow_rate[0:5]
        logger.warning(f'{pump.class_name} flow rate truncated to {flow_rate} uL/min')

    return remove_crud(flow_rate)


def remove_string_formatting_char(string: str) -> str:
    """
    Remove non-printable characters, retaining standard printable ASCII.

    Parameters
    ----------
    string : str

    Returns
    -------
    str
        String containing only characters with ordinals in (31, 126).
    """
    return ''.join(s for s in string if 31 < ord(s) < 126)


class PumpHarvard(SyringePump):
    """
    Harvard Apparatus PHD 2000 syringe pump driver (RS-232 / address-prefixed ASCII).

    Purpose
    -------
    - Subclass of your `SyringePump` base implementing the Pump 11 / PHD 2000 command
      set (e.g., 'VER', 'MMD', 'ULM', 'RAT', 'MLT', 'RUN', 'REV', 'STP').
    - Provides device I/O helpers that guard the shared serial line with a lock
      (via `Serial`) and raises `EquipmentError` on unexpected or missing replies.

    Key behaviors
    -------------
    - Validates pump **address** (00–99) and uniqueness across instances.
    - Computes **min/max flow rates** from geometry and device pull rate range (cm/min).
    - Formats/normalizes **diameter** and **flow rate** device strings,
      checking via echo reads (e.g., 'DIA', 'RAT').
    - Supports **infuse/withdraw** by setting rate + target volume (μL), then issuing 'RUN'.

    Notes
    -----
    - Status characters appear as HTML-escaped '&gt;' / '&lt;' in source. The device sends raw '>' / '<'.
    - This class expects a `Serial` object providing `.write()`, `.read()`, a `.lock`, and `.close()`.
    """

    # Device mechanical limits expressed as plunger travel rates (cm/min)
    pull_rate_min = 0.00002  # cm/min
    pull_rate_max = 19.0     # cm/min
    baud_rates = [1200, 2400, 9600, 19200]

    _address_in_use = set()  # simple process-level guard to reduce address collisions

    def __init__(
            self,
            serial_line: Serial,
            address: int,
            diameter: int | float,  # units: mm
            name: str = None,
            max_volume: float = None,  # units: ml
            max_pull: float = None,    # units: cm
    ):
        """
        Parameters
        ----------
        serial_line : Serial
            Shared serial line wrapper with inter-thread lock.
        address : int
            Device address in 0..99 (two-digit string on the wire).
        diameter : int | float
            Syringe inner diameter in millimeters.
        name : str | None
            Logical name for logs/diagnostics (defaults to class name + id).
        max_volume : float | None
            Optional maximum volume (mL) used to infer default target when none is provided.
        max_pull : float | None
            Optional maximum plunger travel (cm) for safety/limits.
        """
        super().__init__(name, diameter, max_volume, max_pull,
                         control_method=SyringePump.control_methods.flow_rate)
        if name is None:
            self.name = f"{type(self).__name__} (id: {self.id_})"
        self.serial_line = serial_line
        self._address = None
        self.address = address

        # Connectivity probe + initial configuration
        self.ping_pump()
        self.diameter = diameter

    # -------------------------
    # Device capability helpers
    # -------------------------

    @property
    def max_flow_rate(self) -> float:
        """
        Maximum flow rate in mL/min based on geometry and device pull limit.

        Formula
        -------
        Q_max = A * v_max = π (D/2)^2 * pull_rate_max
        (with D in cm to produce mL/min; units assumed consistent in base class)
        """
        return math.pi * (self.diameter / 2)**2 * self.pull_rate_max

    @property
    def min_flow_rate(self) -> float:
        """
        Minimum flow rate in mL/min based on geometry and device pull limit.

        Q_min = π (D/2)^2 * pull_rate_min
        """
        return math.pi * (self.diameter / 2)**2 * self.pull_rate_min

    # -------------------------
    # Address management
    # -------------------------

    @property
    def address(self) -> str:
        """Two-digit, zero-padded device address as a string (e.g., '00', '07', '42')."""
        return self._address

    @address.setter
    def address(self, address: int):
        """
        Validate and assign the device address, guarding duplicates in-process.

        Checks performed
        ----------------
        - Address is an **int**
        - Address in **[0, 99]**
        - Address **not already in use** by another instance (process-local tracking)
        """
        if 0 > address or address > 99:
            raise EquipmentError(self, "Acceptable addresses are from [0, 99].")

        if type(address) is not int:
            raise EquipmentError(self, "Addresses must be an integer.")

        if address in self._address_in_use:
            raise EquipmentError(self, f"Address {address} already taken, so can't be assigned to {self.name}")

        self._address = '{0:02.0f}'.format(address)
        self._address_in_use.add(address)

    # -------------------------
    # Low-level I/O (guarded by Serial lock)
    # -------------------------

    def _write(self, message: str):
        """
        Write a command to the serial line (address + message + CR).

        Warning
        -------
        Not thread safe by itself. Use `_write_read` which acquires the lock
        around a paired write+read cycle.
        """
        self.serial_line.write(self.address + message + '\r')

    def _read(self, bytes_: int = 5) -> str:
        """
        Read a fixed number of bytes from the serial line.

        Parameters
        ----------
        bytes_ : int
            Number of bytes to read.

        Returns
        -------
        str
            Raw response string.

        Raises
        ------
        EquipmentError
            If no response is received (timeout/empty).
        """
        response = self.serial_line.read(bytes_)
        if response is None or len(response) == 0:
            raise EquipmentError(self, 'No response to command.')
        else:
            return response

    def _write_read(self, message: str, bytes_: int = 5) -> list:
        """
        Thread-safe write+read cycle; returns tokenized response list.

        Device protocol
        ---------------
        - Pump uses **write → response** pattern.
        - Multiple pumps may share the same serial line → acquire/release `Serial.lock`.

        Returns
        -------
        list[str]
            Tokenized reply: converts CR/LF to spaces and splits on whitespace.
        """
        self.serial_line.lock.acquire()
        self._write(message)
        response = self._read(bytes_)
        self.serial_line.lock.release()

        return response.replace('\n', " ").replace('\r', " ").split()

    # -------------------------
    # Device setup / queries
    # -------------------------

    def ping_pump(self):
        """
        Probe the pump with 'VER' and validate that the tail includes our address.

        Expected reply
        --------------
        The last three characters are `XXY`:
          - `XX` = address (two digits)
          - `Y`  = status char (':', '>', or '<')

        Raises
        ------
        EquipmentError
            On address mismatch or no reply.
        """
        response = self._write_read('VER', 17)

        # check response
        if int(response[1][:-1]) != int(self.address):
            self.serial_line.close()
            raise EquipmentError(
                self,
                f'No response from pump at address {self.address}\n Check the following: '
                f'\n\t1. All pumps have unique addresses\n\t2. All pumps have the same baud rates.'
            )

    def _diameter_setter(self, diameter: int | float):
        """
        Override hook calling base setter then writing the device.
        """
        super()._diameter_setter(diameter)
        self._set_diameter(diameter)

    def _set_diameter(self, diameter: float):
        """
        Set syringe diameter (millimetres) and verify via echo.

        Device notes
        ------------
        - Pump 11 diameter range is roughly 0.1–35 mm.
        - Firmware ignores precision beyond 2 dp; we format and warn accordingly.

        Raises
        ------
        EquipmentError
            If device reply does not end with a known status, or echo check fails.
        """
        # Send command
        response = self._write_read('MMD' + _format_diameter(self, diameter), 5)

        # check response
        if not (response[0][-1] == ':' or response[0][-1] == '&lt;' or response[0][-1] == '&gt;'):
            raise EquipmentError(self, f'Unknown response to set diameter.')  # TODO: NA
        # Check diameter was set accurately
        if returned_diameter := self.check_diameter() != diameter:
            raise EquipmentError(self, f'Set diameter ({diameter} mm) does not match diameter'
                                       f' returned by pump ({returned_diameter} mm)')

        # log update
        logger.info(f'{self.name}: diameter set to {self.diameter} mm')

    def check_diameter(self) -> float:
        """
        Read back the current diameter ('DIA') as a float.

        Raises
        ------
        EquipmentError
            If the response cannot be parsed as a float.
        """
        response = self._write_read('DIA', 15)
        try:
            return float(response[0])
        except ValueError:
            raise EquipmentError(self, f'Unknown response to check diameter')

    def _set_flow_rate(self, flow_rate: float | int):
        """
        Set flow rate (μL/min) within computed device limits and verify via echo.

        Validation
        ----------
        - Ensures `min_flow_rate <= flow_rate <= max_flow_rate`.
        - Formats/truncates device string and sends 'ULM<value>'.
        - Reads back with `check_flow_rate()` and compares.

        Raises
        ------
        EquipmentError
            If outside limits, device status is unknown, or echo mismatches.
        """
        if not (self.min_flow_rate <= flow_rate <= self.max_flow_rate):
            raise EquipmentError(self, f"Flow rate outside of valid range. Requested: {flow_rate}, "
                                       f"Valid Range: {self.min_flow_rate} -> {self.max_flow_rate}")

        formatted_flow_rate = _format_flow_rate(self, flow_rate)
        response = self._write_read('ULM' + formatted_flow_rate, 5)

        # check response
        if not (response[0][-1] == ':' or response[0][-1] == '&lt;' or response[0][-1] == '&gt;'):
            raise EquipmentError(self, f'Unknown response to set flow rate.')

        # Flow rate was sent, check it was set correctly
        if returned_flow_rate := self.check_flow_rate() != float(formatted_flow_rate):
            raise EquipmentError(self, f"set flow rate ({flow_rate} uL/min) does not match"
                                       f'flow rate returned by pump ({returned_flow_rate} uL/min)')

        self._flow_rate = flow_rate
        logger.info(f"{self.name}: flow rate set to {formatted_flow_rate} uL/min")

    def check_flow_rate(self) -> float:
        """
        Read back the flow rate ('RAT') as a float.

        Raises
        ------
        EquipmentError
            If out-of-range ('OOR') or reply cannot be parsed.
        """
        response = self._write_read('RAT', 15)
        try:
            return float(response[0])
        except ValueError:
            if 'OOR' in response:
                raise EquipmentError(self, 'Flow rate is out of range')

            raise EquipmentError(self, f"Unknown response to 'check flow rate'.")

    def _set_target_volume(self, target_volume: int | float):
        """
        Set target volume (μL) for the run via 'MLT' (uses mL on the wire).

        Notes
        -----
        - Command uses 'mL' units, so this converts μL → mL on send.
        - Device is expected to confirm with a terminal status char.

        Raises
        ------
        EquipmentError
            On unknown device reply.
        """
        response = self._write_read('MLT' + str(target_volume/1000), 5)   # micro-liter -> milli-liter

        # response should be CRLFXX:, CRLFXX>, CRLFXX< where XX is address
        # Pump11 replies with leading zeros, e.g. 03, but PHD2000 misbehaves and
        # returns without and gives an extra CR. Use int() to deal with
        if not (response[0][-1] == ':' or response[0][-1] == '&lt;' or response[0][-1] == '&gt;'):
            raise EquipmentError(self, f'Unknown response to set flow rate.')

        self._target_volume = float(target_volume)
        logger.info(f"{self.name}: target volume set to {target_volume} uL")

    def check_target_volume(self) -> float:
        """
        Read back target volume ('TAR') as a float.

        Raises
        ------
        EquipmentError
            If reply cannot be parsed.
        """
        response = self._write_read('TAR', 15)
        try:
            return float(response[0])
        except ValueError:
            raise EquipmentError(self, f"Unknown response to 'check target volume'.")

    def check_infused_volume(self) -> float:
        """
        Read back delivered (infused) volume ('VOL') as a float.

        Raises
        ------
        EquipmentError
            If reply cannot be parsed.
        """
        response = self._write_read('VOL', 15)
        try:
            return float(response[0])
        except ValueError:
            raise EquipmentError(self, f"Unknown response to 'check target volume'.")

    # -------------------------
    # High-level operations
    # -------------------------

    def infuse(self, flow_rate: int | float, volume: int | float = None):
        """
        Start **infusing** (forward direction).

        Parameters
        ----------
        flow_rate : int | float
            Rate in μL/min.
        volume : int | float, optional
            Target volume in μL. If None, uses `max_volume` if provided,
            otherwise a large sentinel value.

        Behavior
        --------
        - Sets rate and target, then sends 'RUN'. If first attempts show ':',
          retries a few times. If terminal status is '<', issues 'REV' to correct direction.
        """
        # set up everything
        self._set_flow_rate(flow_rate)
        if volume is None:
            if self.max_volume is not None:
                volume = self.max_volume
            else:
                volume = 1_000_000  # large number to ensure it adds everything
        self._set_target_volume(volume)

        # start run
        response = self._write_read('RUN', 5)
        for i in range(10):
            if response[0][1] != ":":
                break
            response = self._write_read('RUN', 5)

        if response[0][-1] == '&lt;':  # wrong direction
            response = self._write_read('REV', 5)

        # if response[0][-1] != '>':  # original check commented by author
        #     raise EquipmentError(self, "Unknown response to 'infuse'")

        logger.info(f"{self.name}: infusing")

    def withdraw(self, flow_rate: int | float, volume: int | float = None):
        """
        Start **withdrawing** (reverse direction).

        Parameters
        ----------
        flow_rate : int | float
            Rate in μL/min.
        volume : int | float, optional
            Target volume in μL. If None, and `max_volume` exists, uses
            `max_volume - self.volume`; otherwise warns and uses a large sentinel.

        Behavior
        --------
        - Sets rate and target, then 'RUN'; retries if ':' is returned.
        - If terminal status is '>', sends 'REV' to correct direction.
        """
        # set up everything
        self._set_flow_rate(flow_rate)
        if volume is None:
            if self.max_volume is not None:
                volume = self.max_volume - self.volume
            else:
                logger.warning(f"withdraw started on pump {self.id_} with no stop.")
                volume = 1_000_000  # large number to ensure it adds everything
        self._set_target_volume(volume)

        # start run
        self._set_flow_rate(flow_rate)
        response = self._write_read('RUN', 5)
        for i in range(10):
            if response[0][1] != ":":
                break
            response = self._write_read('RUN', 5)

        if response[0][-1] == '&gt;':  # wrong direction
            response = self._write_read('REV', 5)

        # if response[0][-1] != '<':  # original check commented by author
        #     raise EquipmentError(self, "Unknown response to 'withdraw'.")

        logger.info(f"{self.name}: withdrawing")

    def stop(self):
        """
        Stop the pump ('STP') and verify stopped status ':'.

        Raises
        ------
        EquipmentError
            If the terminal status is not ':'.
        """
        response = self._write_read('STP', 5)
        if response[0][-1] != ':':
            raise EquipmentError(self, "Unknown response to 'stop'.")

        logger.info(f'{self.name}: stopped')

    # Convenience helpers
    def zero(self):
        """Infuse at ~50% of maximum flow rate (quick zeroing maneuver)."""
        self.infuse(flow_rate=self.max_flow_rate * 0.5)

    def fill(self):
        """Withdraw at ~50% of maximum flow rate (quick fill maneuver)."""
        self.withdraw(flow_rate=self.max_flow_rate * 0.5)

    # -------------------------
    # Profiled run (skeleton)
    # -------------------------

    def run(self, flow_profile: PumpFlowProfile, start_time: int | float = 0):
        """
        Execute a time-profiled flow (skeleton).

        Parameters
        ----------
        flow_profile : PumpFlowProfile
            A time→flow mapping (e.g., sequence of (t, rate) points).
        start_time : float | int
            Epoch start (time.time()) or 0 to start immediately.

        Notes
        -----
        - This method is a placeholder; the logic for stepping through
          the profile and issuing `ULM` updates per segment should be
          implemented according to your ContinuousEventHandler patterns.
        """
        if start_time is None:
            start_time = time.time()

        # first event
        # (implementation intentionally left incomplete per original source)
        # pass


# -------------------------
# Local test helpers
# -------------------------

def local_run_two_pumps():
    """
    Example: share one serial line between two pumps and run the same profile.
    """
    from chembot.communication import Serial
    serial_line = Serial("COM5", baud_rate=9600, parity=Serial.ParityOptions.none, stop_bits=2, bytes_=8, timeout=1)

    pump = PumpHarvard(
        serial_line,
        address=0,
        diameter=1,
        max_volume=10,
    )
    pump2 = PumpHarvard(
        serial_line,
        address=1,
        diameter=1,
        max_volume=10,
    )

    flow_profile = PumpFlowProfile(
        (
            (0, 0),
            (1, 1),
            (2, 1),
            (3, 2),
            (4, 0)
        )
    )

    pump.run(flow_profile)
    pump2.run(flow_profile)


def local_flow_profile():
    """
    Example: run a simple profile on a single pump.
    """
    from chembot.communication import Serial
    serial_line = Serial("COM5", baud_rate=9600, parity=Serial.ParityOptions.none, stop_bits=2, bytes_=8, timeout=1)

    pump = PumpHarvard(
        serial_line,
        address=0,
        diameter=1,
        max_volume=10,
    )
    flow_profile = PumpFlowProfile(
        (
            (0, 0),
            (1, 1),
            (2, 1),
            (3, 2),
            (4, 0)
        )
    )
    pump.run(flow_profile)


def local_run_single():
    """
    Example: open a port, create one pump, and issue a stop.
    """
    from chembot.communication import Serial
    serial_line = Serial("COM5", baud_rate=19200, parity=Serial.ParityOptions.none, stop_bits=2, bytes_=8, timeout=1)

    pump = PumpHarvard(
        serial_line,
        address=0,
        diameter=14.2,
        max_volume=10000,
    )
    # pump.zero()
    # pump.withdraw(1, 1)
    # pump.infuse(1, 1)
    pump.stop()


if __name__ == '__main__':
    local_run_single()