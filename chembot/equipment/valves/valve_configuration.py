from __future__ import annotations
import re
from typing import Iterator


def create_selector_valve(config: str) -> tuple[tuple[int, int]]:
    """
    Create a selector-valve configuration from a string like "6S" or "10S".

    Purpose
    -------
    - For a selector valve, the leading integer defines the number of ports.
      ("6S" → 6-port selector valve)
    - Each selector position corresponds to connecting:
          port_0  <-->  port_i
      for i = 1 .. number_ports

    Parameters
    ----------
    config : str
        Selector valve abbreviation (e.g., "8S").

    Returns
    -------
    tuple[tuple[int, int]]
        A tuple of 2-element tuples describing each valve position's port
        connections. Example:
            ( (0,1), (0,2), (0,3), ... )

    Raises
    ------
    ValueError
        If the string does not begin with an integer.
    """
    match = re.match(r'^\d+', config)
    if match:
        number_ports = int(match.group())
    else:
        raise ValueError(f"Invalid selector port configuration.\nInvalid: {config}")

    return tuple((0, i) for i in range(1, number_ports + 1))


class ValvePort:
    """
    Representation of a single physical valve port.

    Attributes
    ----------
    id_ : int
        Zero-based port index.
    _name : str | None
        Optional human-readable name.
    blocked : bool
        For valves with dead ports or blanked-off connections.
    """
    __slots__ = ("id_", "_name", "blocked")

    def __init__(self, id_: int, name: str = None, blocked: bool = False):
        self.id_ = id_
        self._name = name
        self.blocked = blocked

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.__str__()

    @property
    def name(self) -> str:
        """
        Human-readable port name. Defaults to "port_<id_>".
        """
        if self._name is not None:
            return self._name
        return f"port_{self.id_}"

    @name.setter
    def name(self, name: str):
        self._name = name


class ValveChannel:
    """
    A channel is a group of ports that are connected together in a single
    valve position.

    Example
    -------
    - A simple 2-way path     → (port_0 <--> port_1)
    - A T-valve channel       → (port_1 <--> port_2 <--> port_3)
    """
    __slots__ = ("connections",)

    def __init__(self, connections: tuple[ValvePort]):
        self.connections = connections

    def __str__(self):
        return " <--> ".join(str(port) for port in self.connections)

    def __repr__(self):
        return self.__str__()

    @property
    def number_ports(self) -> int:
        return len(self.connections)


class ValvePosition:
    """
    Describes one discrete physical position of a rotary or selector valve.

    Attributes
    ----------
    id_ : int
        Zero-based index of this valve position.
    channels : tuple[ValveChannel]
        One or more parallel fluidic channels active at this position.
    name : str | None
        Human-friendly label ("load", "inject", etc.).
    setting : Any
        Optional metadata (e.g., microstep count for stepper-driven valves).
    """

    def __init__(
        self,
        id_: int,
        channels: tuple[ValveChannel],
        name: str = None,
        setting=None
    ):
        self.id_ = id_
        self.channels = channels
        self._name = name
        self.setting = setting

    def __str__(self):
        return " and ".join(str(port) for port in self.channels)

    def __repr__(self):
        return self.__str__()

    @property
    def name(self) -> str:
        """Human-readable name; defaults to 'position_<id_>'."""
        if self._name is not None:
            return self._name
        return f"position_{self.id_}"

    @name.setter
    def name(self, name: str):
        self._name = name

    @property
    def number_of_channels(self) -> int:
        """Number of fluidic channels active in this position."""
        return len(self.channels)


class ValveConfiguration:
    """
    Full description of a valve type:
    - its abbreviation (e.g., '4L', '6TL', '3TZ')
    - its list of ValvePositions
    - its list of ValvePorts

    Provides dictionary-like access, iteration, and helper metadata.

    Class Attributes
    ---------------
    valve_configs : dict[str, tuple]
        Predefined port-connection templates for many commercial valve types.
        Keys include:
            "2", "3L", "3LZ", "3TZ", "3S", "4L", "4LL", "4T", "4", "6TL"
    """

    valve_configs = {
        "2": ((0, 2), (1, 3)),
        "3L": ((0, 1), (1, 2)),
        "3LZ": ((0, 1), (1, 2), (2, 3), (3, 0)),
        "3TZ": ((0, 1, 2), (1, 2, 3), (2, 3, 0), (1, 0, 3)),
        "3S": ((0, 1), (0, 2), (0, 3)),
        "4L": ((0, 1), (1, 2), (2, 3), (3, 0)),
        "4LL": (
            ((0, 1), (2, 3)),
            ((1, 2), (3, 0)),
            ((2, 3), (0, 1)),
            ((3, 0), (1, 2)),
        ),
        "4T": ((0, 1, 2), (1, 2, 3), (2, 3, 0), (3, 0, 1)),
        "4": ((0, 2), (1, 3), (2, 0), (3, 1)),
        "6TL": (
            ((0, 1), (2, 3), (4, 5)),
            ((6, 0), (1, 2), (3, 4)),
        ),
    }

    def __init__(
        self,
        abbreviation: str,
        positions: list[ValvePosition],
        ports: list[ValvePort]
    ):
        self.abbreviation = abbreviation
        self.positions = positions
        self.ports = ports

    def __str__(self):
        """Summary of all valve positions."""
        return " || ".join(str(port) for port in self.positions)

    def __repr__(self):
        return self.__str__()

    def __iter__(self) -> Iterator[ValvePosition]:
        return iter(self.positions)

    def __getitem__(self, item: int | str) -> ValvePosition:
        """
        Retrieve a ValvePosition by:
        - index (int)
        - name (str)

        Raises
        ------
        IndexError
            If an integer is outside the valid range.
        ValueError
            If a string name does not match any position.
        """
        if isinstance(item, int):
            try:
                return self.positions[item]
            except IndexError as e:
                raise IndexError(
                    f"Valve position outside valid range: "
                    f"[0, {self.number_of_positions - 1}]"
                )

        if isinstance(item, str):
            for pos in self.positions:
                if item == pos.name:
                    return pos
            raise ValueError(
                f"Invalid valve position.\nInvalid position: {item}"
                f"\nOptions: {[pos.name for pos in self.positions]}"
            )

        raise ValueError(
            f"Invalid {type(self).__name__}.__getitem__ parameter.\nInvalid:{item}"
        )

    # -------------------------
    # Convenience metadata
    # -------------------------

    @property
    def number_of_ports(self) -> int:
        return len(self.ports)

    @property
    def number_of_positions(self) -> int:
        return len(self.positions)

    @property
    def number_of_channels(self) -> int:
        """Assumes all positions have the same channel count."""
        return self.positions[0].number_of_channels

    # -------------------------
    # Factory constructor
    # -------------------------

    @classmethod
    def get_configuration(cls, config: str) -> ValveConfiguration:
        """
        Factory method for building a ValveConfiguration from a short-form
        code (e.g., '4L', '3TZ', '6TL', '8S').

        Behavior
        --------
        - If config ends in "S", build a selector valve of size N.
        - Otherwise, match against predefined `valve_configs`.
        - Convert the tuple-based port specifications into actual
          ValvePort, ValveChannel, and ValvePosition objects.

        Returns
        -------
        ValveConfiguration

        Raises
        ------
        ValueError
            If the configuration string is invalid.
        """

        if config.endswith("S"):
            valve_config = create_selector_valve(config)
        elif config in cls.valve_configs:
            valve_config = cls.valve_configs[config]
        else:
            raise ValueError("Invalid valve configuration.")

        # Convert tuple structure → full port/position objects
        ports = []
        positions = []
        for i, position in enumerate(valve_config):
            positions.append(create_position(ports, position, i))

        return cls(config, positions, ports)


# -------------------------
# Helper construction functions
# -------------------------

def create_position(ports: list[ValvePort], position, position_index: int):
    """
    Convert a raw tuple describing one valve position into a ValvePosition.

    Parameters
    ----------
    ports : list[ValvePort]
        Master list of ports; new ones created on demand.
    position : tuple
        A tuple describing each channel, e.g.:
           (0,1) or ((0,1),(2,3))
    position_index : int
        ID for this ValvePosition.

    Returns
    -------
    ValvePosition
    """
    if not isinstance(position[0], tuple):
        position = (position,)

    channels = []
    for channel in position:
        channels.append(create_channel(ports, channel))

    return ValvePosition(position_index, tuple(channels))


def create_channel(ports: list[ValvePort], channel: tuple[int]) -> ValveChannel:
    """
    Convert a tuple of port indices into a ValveChannel.

    Example
    -------
        channel = (1, 3, 5)
        → ValveChannel( ports[1], ports[3], ports[5] )

    Ports are created dynamically if needed.
    """
    connections = []
    for port in channel:
        if port >= len(ports):
            ports.append(ValvePort(port))
        connections.append(ports[port])

    return ValveChannel(tuple(connections))