import abc
import logging

from chembot.configuration import config
from chembot.equipment.valves.valve_configuration import ValveConfiguration, ValvePosition
from chembot.equipment.equipment import Equipment

logger = logging.getLogger(config.root_logger_name + ".valve")


class Valve(Equipment, abc.ABC):
    """
    Abstract base class for any multi-position valve in the Chembot system.

    Purpose
    -------
    - Provide a unified interface for all physical valve devices (rotary valves,
      solenoid arrays, selection valves, fractionation valves, etc.).
    - Enforce consistent behavior across implementations via:
        * a valve configuration object (ValveConfiguration)
        * abstract movement method `_move()`
        * required high-level control API (`write_move`, `write_move_next`, etc.)
    - Manage valve state transitions in a safe, predictable manner.

    How it fits into Chembot
    ------------------------
    - Inherits from `Equipment`, meaning the valve participates in Chembot’s
      lifecycle hooks (`_activate`, `_deactivate`, `_stop`) and message routing.
    - Works with a `ValveConfiguration`, which provides:
        * A list of valid `ValvePosition` objects
        * The ability to look up positions by integer, string, or ID
        * Number of total positions
    """

    def __init__(
        self,
        name: str,
        configuration: ValveConfiguration
    ):
        """
        Parameters
        ----------
        name : str
            Logical device name for Chembot messaging and logs.
        configuration : ValveConfiguration
            Object describing the valid positions for this valve.

        Notes
        -----
        - Initial valve position is always set to the *first* defined position
          in the configuration object.
        """
        super().__init__(name=name)
        self.configuration = configuration

        # Current physical position of the valve (ValvePosition object)
        self.position: ValvePosition = self.configuration.positions[0]

    def __repr__(self):
        """
        Human-readable debug representation of the valve state.
        """
        return (
            f"\nValve: {self.name}\n"
            f"\tconfig: {self.configuration}\n"
            f"\tstate: {self.state}\n"
            f"\tcurrent position: {self.position}\n"
        )

    # -------------------------
    # Lifecycle hooks
    # -------------------------

    def _activate(self):
        """
        On activation, automatically move the valve to its default
        (first) position.

        This ensures the physical state matches the logical state.
        """
        self._move(self.configuration.positions[0])

    # -------------------------
    # Public API methods
    # -------------------------

    def read_position(self) -> ValvePosition:
        """
        Returns
        -------
        ValvePosition
            The valve's current internal state (not necessarily guaranteed
            to reflect physical state if `_move()` fails silently).
        """
        return self.position

    def write_move(self, position: int | str | ValvePosition):
        """
        Move valve to a specific position.

        Parameters
        ----------
        position : int | str | ValvePosition
            - If int: interpreted as position ID or index
            - If str: interpreted via configuration’s dictionary mapping
            - If ValvePosition: used directly

        Behavior
        --------
        - Converts the input to a ValvePosition instance using the configuration.
        - Calls `_move()` to perform the hardware-specific actuation.
        - Updates internal `self.position` upon successful action.
        """
        if not isinstance(position, ValvePosition):
            position = self.configuration[position]

        self._move(position)
        self.position = position

    def write_move_next(self):
        """
        Rotate to the next valve position (wraps around cyclically).

        Example
        -------
        If valve has positions [0,1,2] and currently at 2,
        this moves to 0.
        """
        if self.position is self.configuration.positions[-1]:
            position = 0  # wrap around
        else:
            position = self.position.id_ + 1

        self.write_move(position)

    def write_move_back(self):
        """
        Rotate to the previous valve position (wraps around cyclically).

        Example
        -------
        If valve has positions [0,1,2] and currently at 0,
        this moves to 2.
        """
        if self.position is self.configuration.positions[0]:
            position = self.configuration.number_of_positions - 1  # wrap around
        else:
            position = self.position.id_ - 1

        self.write_move(position)

    # -------------------------
    # Abstract hardware method
    # -------------------------

    @abc.abstractmethod
    def _move(self, position: ValvePosition):
        """
        Low-level hardware actuation method.

        Must be implemented by subclasses for:
        - serial-controlled rotary valves
        - motor-driven multiport valves
        - solenoid valve arrays
        - microfluidic MEMS valves
        - simulated valves for testing

        Notes
        -----
        - Should block until the valve has physically reached the target position.
        - Should raise or log errors on device faults.
        """
        