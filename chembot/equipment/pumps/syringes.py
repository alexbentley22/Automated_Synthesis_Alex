from __future__ import annotations

import numpy as np
from unitpy import Quantity, Unit

from chembot.utils.unit_validation import validate_quantity


class Syringe:
    """
    Immutable parameters and convenience utilities for a syringe definition.

    Purpose
    -------
    - Represent a syringe by any two of the three geometric parameters:
        * volume (V),
        * inner diameter (D),
        * plunger travel (pull, L).
      The third parameter is computed from the other two using cylinder geometry.
    - Store a default flow rate (optional) and derived defaults (e.g., default fill time).
    - Provide unit-safe properties using `unitpy` and centralized dimensionality validation.

    Typical usage
    -------------
        s = Syringe(volume=10 * Unit("mL"), diameter=9.5 * Unit("mm"), name="10 mL BD")
        print(s.pull)  # computed plunger travel
        print(s.default_flow_rate)  # if not supplied, falls back to volume / default_fill_time

    Notes
    -----
    - You **must** provide exactly **two** of (volume, diameter, pull). If fewer or more
      are given, a `ValueError` is raised to avoid ambiguous definitions.
    - All values are validated for dimensionality and positivity via `validate_quantity`.
    """

    # -------------------------
    # Class-level defaults / dimensionalities
    # -------------------------

    # Default time to fill the syringe if no default flow rate is provided
    default_fill_time = 2 * Unit.min

    # Dimensionality guards used by validate_quantity
    volume_dimensionality = Unit("liter").dimensionality
    diameter_dimensionality = Unit("meter").dimensionality
    pull_dimensionality = diameter_dimensionality  # length
    flow_rate_dimensionality = Unit('mL/min').dimensionality

    def __init__(self,
                 volume: Quantity = None,
                 diameter: Quantity = None,
                 pull: Quantity = None,
                 default_flow_rate: Quantity = None,
                 force: int = None,
                 name: str = None,
                 vendor: str = None,
                 **kwargs
                 ):
        """
        Parameters
        ----------
        volume : Quantity | None
            Syringe internal volume. (Set **2 of 3**: volume, diameter, pull.)
        diameter : Quantity | None
            Inner diameter of the syringe. (Set **2 of 3**.)
        pull : Quantity | None
            Plunger travel (length). (Set **2 of 3**.)
        default_flow_rate : Quantity | None
            Optional default flow rate; if omitted, computed as volume / default_fill_time.
        force : int | None
            Optional nominal force capability/setting (metadata; not validated here).
        name : str | None
            Human-friendly name (defaults to `"syringe: <volume>"` if not provided).
        vendor : str | None
            Vendor name or model.
        **kwargs
            Any additional metadata you wish to attach as attributes.

        Raises
        ------
        ValueError
            If not exactly two of (volume, diameter, pull) are provided.
        """
        # Ensure exactly 2 of 3 are supplied
        if sum(1 for i in (volume, diameter, pull) if i is not None) != 2:
            raise ValueError("Provide 2 of 3 values to define a syringe: volume, diameter, pull")

        # Backing fields
        self._volume = None
        self._diameter = None
        self._pull = None
        self._default_flow_rate = None

        # Assign (validated) primary parameters
        self.volume = volume
        self.diameter = diameter
        self.pull = pull

        # Flow / force metadata
        self.default_flow_rate = default_flow_rate
        self.force = force

        # Identity/metadata
        self.vendor = vendor
        self.name = name if name is not None else f"syringe: {self.volume}"

        # Compute the missing third parameter from the two provided
        self._compute_missing_parameter()

        # Attach extra metadata
        if kwargs:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def __str__(self):
        return f"{self.name} || volume: {self.volume}, diameter: {self.diameter}"

    # -------------------------
    # Properties with validation
    # -------------------------

    @property
    def volume(self) -> Quantity:
        """Syringe internal volume (unit-safe)."""
        return self._volume

    @volume.setter
    def volume(self, volume: Quantity):
        if volume is None:
            return
        validate_quantity(volume, self.volume_dimensionality, f"Syringe.volume", positive=True)
        self._volume = volume

    @property
    def diameter(self) -> Quantity:
        """Syringe inner diameter (unit-safe)."""
        return self._diameter

    @diameter.setter
    def diameter(self, diameter: Quantity):
        if diameter is None:
            return
        validate_quantity(diameter, self.diameter_dimensionality, f"Syringe.diameter", positive=True)
        self._diameter = diameter

    @property
    def pull(self) -> Quantity:
        """Plunger travel (length; unit-safe)."""
        return self._pull

    @pull.setter
    def pull(self, pull: Quantity):
        if pull is None:
            return
        validate_quantity(pull, self.pull_dimensionality, f"Syringe.pull", positive=True)
        self._pull = pull

    @property
    def default_flow_rate(self) -> Quantity:
        """
        Default flow rate (unit-safe).

        Behavior
        --------
        - If no explicit default is set, returns `volume / default_fill_time`.
        - Otherwise returns the stored `_default_flow_rate`.
        """
        if self._default_flow_rate is None:
            return self.volume / self.default_fill_time
        return self._default_flow_rate

    @default_flow_rate.setter
    def default_flow_rate(self, default_flow_rate: Quantity):
        """
        Set the default flow rate. If None, derive from volume and default fill time.
        """
        if default_flow_rate is None:
            default_flow_rate = self.volume / self.default_fill_time
        validate_quantity(default_flow_rate, self.flow_rate_dimensionality, f"Syringe.default_flow_rate", positive=True)
        self._default_flow_rate = default_flow_rate

    # -------------------------
    # Internal helpers
    # -------------------------

    def _compute_missing_parameter(self):
        """
        Compute the third syringe parameter from the two provided.

        Geometry
        --------
        Cylinder volume:  V = π * (D/2)^2 * L
        - If D and V provided → compute L (pull)
        - If V and L provided → compute D (diameter)
        - If L and D provided → compute V (volume)

        Notes
        -----
        - Uses numpy for π and exponentiation; results retain unit safety through operations.
        - Executes only when the corresponding properties are present/absent as required.
        """
        if self.diameter is not None and self.volume is not None and self.pull is None:
            self.pull = self.volume / (np.pi * (self.diameter / 2) ** 2)

        if self.volume is not None and self.pull is not None and self.diameter is None:
            self.diameter = (self.volume / self.pull / np.pi) ** (1 / 2) * 2

        if self.pull is not None and self.diameter is not None and self.volume is None:
            self.volume = self.pull * np.pi * (self.diameter / 2) ** 2

    # -------------------------
    # Factory helpers
    # -------------------------

    @classmethod
    def get_syringe(cls, name: str) -> Syringe:
        """
        Construct a `Syringe` by name from your reference config.

        Parameters
        ----------
        name : str
            Key in `chembot.reference_data.syringe_configs`.

        Returns
        -------
        Syringe

        Raises
        ------
        KeyError
            If `name` does not exist in the configuration mapping.
        """
        from chembot.reference_data.syringe_configs import syringe_configs
        if name in syringe_configs:
            return Syringe(name=name, **syringe_configs[name])
        raise KeyError(f"'{name}' not within syringe configuration.")