import abc
import math
import enum
import logging

from unitpy import Quantity, Unit

from chembot.configuration import config
from chembot.equipment.equipment import Equipment
from chembot.equipment.pumps.syringes import Syringe
from chembot.utils.unit_validation import validate_quantity

logger = logging.getLogger(config.root_logger_name + ".pump")


class PumpControlMethod(enum.Enum):
    """
    Control strategies a pump *could* support.

    Notes
    -----
    - The current base class keeps this as a placeholder for future expansion.
    - Concrete drivers may expose one or both methods.
    """
    flow_rate = 0
    pressure = 1


class SyringePumpStatus(enum.Enum):
    """
    Generic, device-agnostic syringe pump states.
    """
    STANDBY = "standby"
    INFUSE = "infuse"
    WITHDRAW = "withdraw"
    STALLED = "stalled"
    TARGET_REACHED = "target_reached"


class SyringeState:
    """
    Captures the dynamic (runtime) state of a syringe and pump.

    This state object is stored on each `SyringePump` instance to keep
    track of current values for UI/telemetry/logic without re-querying
    the device for every small change.

    Attributes
    ----------
    state : SyringePumpStatus | None
        Current high-level pump state (STANDBY/INFUSE/WITHDRAW/…).
    volume_in_syringe : Quantity | None
        Current absolute volume in the syringe barrel.
    volume_displace : Quantity | None
        Accumulated displaced volume since the last reset.
    target_volume : Quantity | None
        Target volume for the current/next run (if set).
    flow_rate : Quantity | None
        Current flow rate (unit-safe). Defaults to 0 ml/min.
    running_time : Quantity | None
        Running time since start (optional).
    end_time : Quantity | None
        Expected end time/duration (optional).
    syringe : Syringe
        The currently configured syringe (diameter, max volume, etc.).
    _max_pull : Quantity | None
        Optional user limit for allowable plunger travel (safety cap).
    """

    __slots__ = ("state", "_volume_in_syringe", "volume_displace", "target_volume",
                 "flow_rate", "running_time", "end_time", "max_volume", "syringe", "_max_pull")

    def __init__(self, syringe: Syringe, max_pull: Quantity = None):
        self.state: SyringePumpStatus | None = None
        self._volume_in_syringe: Quantity | None = None
        self.volume_displace: Quantity | None = None
        self.target_volume: Quantity | None = None
        self.flow_rate: Quantity | None = 0 * Unit("ml/min")
        self.running_time: Quantity | None = None
        self.end_time: Quantity | None = None
        self.syringe = syringe
        self._max_pull = max_pull

    @property
    def volume_in_syringe(self) -> Quantity | None:
        """Current absolute volume in syringe (unit-safe)."""
        return self._volume_in_syringe

    @volume_in_syringe.setter
    def volume_in_syringe(self, volume_in_syringe: Quantity):
        """
        Set (and sanity-check) current volume in the syringe.

        Logs an error if a negative volume or a volume exceeding syringe capacity is set.
        """
        self._volume_in_syringe = volume_in_syringe
        if self._volume_in_syringe.v < 0:
            logger.error("Volume in syringe went negative!")
        if self._volume_in_syringe > self.syringe.volume:
            logger.error("Volume in syringe over max!")

    @property
    def pull(self) -> Quantity | None:
        """
        Current plunger travel (length) computed from volume and diameter.

        Returns
        -------
        Quantity | None
            Length with units of distance (same dimensionality as Syringe.pull_dimensionality).
        """
        # compute from volume
        return SyringePump.compute_pull(self.syringe.diameter, self.volume_in_syringe)

    @property
    def max_pull(self) -> Quantity:
        """
        Maximum allowable plunger travel.

        Returns the lesser of:
          - user-provided `_max_pull` (if it is more restrictive than syringe spec), and
          - the syringe's intrinsic max pull (from syringe definition).
        """
        if self._max_pull is not None and self.syringe.pull > self._max_pull:
            return self._max_pull
        return self.syringe.pull

    def within_max_pull(self, delta_pull: Quantity, direction: bool = True) -> bool:
        """
        Check whether applying `delta_pull` would remain within limits.

        Parameters
        ----------
        delta_pull : Quantity
            Proposed change in plunger travel (length).
        direction : bool
            True for positive/pull direction; False for push. (See note)

        Returns
        -------
        bool
            True if resulting pull stays within (0, max_pull]; False otherwise.

        Note
        ----
        The arithmetic here follows the original code exactly (no logic changed).
        The `direction` handling is minimal and may not reflect intended semantics;
        consider revisiting if you rely on this method for safety checks.
        """
        if not direction:
            delta_pull = -1 * direction  # kept as-is (direction is a bool → potential bug in original code)

        total_pull = self.pull + delta_pull
        if 0 < total_pull.v:
            return False
        if total_pull > self.max_pull:
            return False

        return True


class SyringePump(Equipment, abc.ABC):
    """
    Abstract base class for syringe pumps in the Chembot system.

    Responsibilities
    ----------------
    - Owns a `Syringe` definition (diameter, volume, limits).
    - Maintains a mutable `SyringeState` (volumes, flow, timing).
    - Provides utility geometry conversions between volume, pull, and diameter.
    - Exposes a minimal read/write API used by concrete pump drivers.
    - Integrates with `Equipment` lifecycle, state model, and messaging.

    Subclassing
    -----------
    Concrete drivers (e.g., Harvard, NewEra, custom controllers) should:
      - implement device-specific actions (start/stop, set rate/volume, read status),
      - keep `self.pump_state` up to date,
      - validate and normalize units with `validate_quantity`.
    """

    control_methods = PumpControlMethod
    pump_states = SyringePumpStatus

    def __init__(self,
                 name: str,
                 syringe: Syringe,
                 max_pull: Quantity = None,
                 # control_method: PumpControlMethod = PumpControlMethod.flow_rate,
                 ):
        super().__init__(name)
        self.syringe = syringe
        # self.control_method = control_method
        self.pump_state = SyringeState(self.syringe, max_pull)
        # TODO: check max and min flow rates

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.__repr__()  # original behavior preserved (though this recurses if called directly)

    # -------------------------
    # Read/Write accessors
    # -------------------------

    def read_syringe(self) -> Syringe:
        """Get the currently configured syringe definition."""
        return self.syringe

    def read_flow_rate(self) -> Quantity:
        """Get the current flow rate from cached state."""
        return self.pump_state.flow_rate

    def write_syringe(self, syringe: Syringe):
        """
        Set (replace) the configured syringe definition.

        Notes
        -----
        - Concrete drivers may also write syringe parameters to hardware.
        - Consider resetting related state fields if syringe properties change materially.
        """
        self.syringe = syringe

    def read_pump_state(self) -> SyringeState:
        """
        Return the full mutable `SyringeState` object (for UIs/introspection).

        Returns
        -------
        SyringeState
            Current cached state of the pump and syringe.
        """
        return self.pump_state

    # -------------------------
    # Geometry / kinematics helpers
    # -------------------------

    @staticmethod
    def compute_run_time(volume: Quantity, flow_rate: Quantity) -> Quantity:
        """
        Compute duration (time) required to move a `volume` at a given `flow_rate`.

        Parameters
        ----------
        volume : Quantity
            Target volume to move.
        flow_rate : Quantity
            Flow rate.

        Returns
        -------
        Quantity
            Unit-safe time quantity (e.g., seconds).

        Raises
        ------
        TypeError / ValueError
            If dimensionalities do not match expectations.
        """
        validate_quantity(volume, Syringe.volume_dimensionality, "volume", True)
        validate_quantity(flow_rate, Syringe.flow_rate_dimensionality, "flow_rate", True)
        duration = abs(volume / flow_rate)
        return duration

    @staticmethod
    def compute_pull(diameter: Quantity, volume: Quantity) -> Quantity:
        """
        Convert volume to plunger travel length for a given inner diameter.

        Parameters
        ----------
        diameter : Quantity
            Syringe inner diameter.
        volume : Quantity
            Volume to convert.

        Returns
        -------
        Quantity
            Plunger travel (length) with unit-safe dimensionality.
        """
        validate_quantity(diameter, Syringe.diameter_dimensionality, "diameter", True)
        validate_quantity(volume, Syringe.volume_dimensionality, "volume", True)
        return volume / (math.pi * (diameter / 2) ** 2)

    @staticmethod
    def compute_volume(diameter: Quantity, pull: Quantity) -> Quantity:
        """
        Convert plunger travel length to volume for a given inner diameter.

        Parameters
        ----------
        diameter : Quantity
            Syringe inner diameter.
        pull : Quantity
            Plunger travel (length).

        Returns
        -------
        Quantity
            Volume corresponding to the travel and area.

        Note
        ----
        The validation call for `diameter` mirrors the original code exactly.
        """
        validate_quantity(diameter, Syringe.volume_dimensionality, "diameter", True)  # kept as-is (likely a typo upstream)
        validate_quantity(pull, Syringe.pull_dimensionality, "pull", True)
        return math.pi * (diameter / 2) ** 2 * pull

    @staticmethod
    def compute_diameter(volume: Quantity, pull: Quantity) -> Quantity:
        """
        Compute inner diameter required to displace a `volume` over a `pull`.

        Parameters
        ----------
        volume : Quantity
            Desired volume.
        pull : Quantity
            Available plunger travel (length).

        Returns
        -------
        Quantity
            Inner diameter (length).
        """
        validate_quantity(pull, Syringe.pull_dimensionality, "pull", True)
        validate_quantity(volume, Syringe.volume_dimensionality, "volume", True)
        return 2 * (volume / (math.pi * pull)) ** (1 / 2)