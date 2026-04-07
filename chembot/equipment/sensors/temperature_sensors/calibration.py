import abc
import math
import logging

from unitpy import Unit, Quantity

from chembot.utils.unit_validation import validate_quantity
from chembot.configuration import config

logger = logging.getLogger(config.root_logger_name + ".temperature")


class ThermalCalibration:
    """
    Base class for converting a measured physical quantity (e.g., resistance)
    into a temperature using a specific calibration model.

    Purpose
    -------
    - Defines shared validation and safety checks for temperature conversions.
    - Stores allowable **temperature limits**, automatically validating every
      computed temperature against them.
    - Enforces that subclasses implement `to_temperature()`, the main
      calibration equation.

    Fields
    ------
    resistance_dimensionality : unitpy dimensionality
        Ensures that resistance inputs have units compatible with Ohms.
    temperature_dimensionality : unitpy dimensionality
        Ensures that temperature outputs/inputs match Kelvin dimensionality.

    Notes
    -----
    - All dimensional checks rely on `validate_quantity()`.
    - `check_temperature_limits()` logs an error if computed temperature is
      outside the calibrated range.
    """

    resistance_dimensionality = Unit("ohm").dimensionality
    temperature_dimensionality = Unit("K").dimensionality

    def __init__(
        self,
        temperature_limit_min: Quantity,
        temperature_limit_max: Quantity,
    ):
        # Internal storage uses private attributes; setters apply validation.
        self._temperature_limit_min = None
        self.temperature_limit_min = temperature_limit_min
        self._temperature_limit_max = None
        self.temperature_limit_max = temperature_limit_max

    # -------------------------
    # Limit getters/setters
    # -------------------------

    @property
    def temperature_limit_min(self) -> Quantity:
        """Lower bound of valid temperature range."""
        return self._temperature_limit_min

    @temperature_limit_min.setter
    def temperature_limit_min(self, temperature_limit_min: Quantity):
        validate_quantity(
            temperature_limit_min,
            self.temperature_dimensionality,
            f"{type(self).__name__}.temperature_limit_min",
        )
        self._temperature_limit_min = temperature_limit_min

    @property
    def temperature_limit_max(self) -> Quantity:
        """Upper bound of valid temperature range."""
        return self._temperature_limit_max

    @temperature_limit_max.setter
    def temperature_limit_max(self, temperature_limit_max: Quantity):
        validate_quantity(
            temperature_limit_max,
            self.temperature_dimensionality,
            f"{type(self).__name__}.temperature_limit_max",
        )
        self._temperature_limit_max = temperature_limit_max

    # -------------------------
    # Validation and interface
    # -------------------------

    def check_temperature_limits(self, temperature: Quantity):
        """
        Log an error if `temperature` is outside [limit_min, limit_max].

        Does not raise by default—just logs—so that calling code can decide
        how to handle excursions.
        """
        if not (self.temperature_limit_min < temperature < self.temperature_limit_max):
            logger.error(
                f"Temperature outside bound of calibration.\n\tReading: {temperature}"
                f"\n\tRange: [{self.temperature_limit_min}, {self.temperature_limit_max}]"
            )

    @abc.abstractmethod
    def to_temperature(self, arg: Quantity) -> Quantity:
        """
        Convert an input Quantity (typically resistance) into a temperature.

        Must be implemented by subclasses using a specific calibration law.

        Returns
        -------
        Quantity
            Temperature with units of Kelvin or convertible from Kelvin.
        """
        pass


class ThermistorCalibrationB(ThermalCalibration):
    """
    "B‑Parameter" thermistor calibration model.

    Purpose
    -------
    - Implements a common exponential thermistor model:
        T = B*T0 / (T0 * ln(R/R0) + B)

    Parameters
    ----------
    B : Quantity
        Thermistor B‑coefficient (units of Kelvin).
    resistance_min : Quantity
        Reference resistance at `temperature_min`.
    temperature_min : Quantity
        Reference temperature associated with `resistance_min`.

    Notes
    -----
    - All dimensional validation is enforced using unitpy.
    - Returned temperature includes unit conversion and safety checks.
    """

    def __init__(
        self,
        B: Quantity,
        resistance_min: Quantity,
        temperature_min: Quantity,
        temperature_limit_min: Quantity,
        temperature_limit_max: Quantity,
    ):
        super().__init__(temperature_limit_min, temperature_limit_max)

        self._B = None
        self.B = B

        self._resistance_min = None
        self.resistance_min = resistance_min

        self._temperature_min = None
        self.temperature_min = temperature_min

    # ---- property wrappers with validation ----

    @property
    def B(self) -> Quantity:
        return self._B

    @B.setter
    def B(self, B: Quantity):
        validate_quantity(
            B,
            self.temperature_dimensionality,
            f"{type(self).__name__}.B",
        )
        self._B = B

    @property
    def resistance_min(self) -> Quantity:
        return self._resistance_min

    @resistance_min.setter
    def resistance_min(self, resistance_min: Quantity):
        validate_quantity(
            resistance_min,
            self.resistance_dimensionality,
            f"{type(self).__name__}.resistance_min",
        )
        self._resistance_min = resistance_min

    @property
    def temperature_min(self) -> Quantity:
        return self._temperature_min

    @temperature_min.setter
    def temperature_min(self, temperature_min: Quantity):
        validate_quantity(
            temperature_min,
            self.temperature_dimensionality,
            f"{type(self).__name__}.temperature_min",
        )
        self._temperature_min = temperature_min

    # ---- core calibration ----

    def to_temperature(self, resistance: Quantity) -> Quantity:
        """
        Compute temperature from resistance using the B‑parameter thermistor equation.

        Parameters
        ----------
        resistance : Quantity
            Measured electrical resistance of the thermistor.

        Returns
        -------
        Quantity
            Temperature (converted automatically to correct units).
        """
        validate_quantity(
            resistance,
            self.resistance_dimensionality,
            f"{type(self).__name__}.resistance",
            True,
        )

        # T = B*T0 / (T0 * ln(R/R0) + B)
        temperature = (
            self.B
            * self.temperature_min
            / (
                self.temperature_min
                * math.log((resistance / self.resistance_min).value)
                + self.B
            )
        )

        self.check_temperature_limits(temperature)
        return temperature


class ThermistorCalibrationSH(ThermalCalibration):
    """
    Steinhart–Hart thermistor calibration model.

    Purpose
    -------
    Implements the Steinhart–Hart equation for high‑accuracy thermistor
    temperature conversion:

        1/T = a + b*ln(R) + c*[ln(R)]^3

    Parameters
    ----------
    a, b, c : float or Quantity-like
        Steinhart–Hart coefficients.
    temperature_limit_min, temperature_limit_max : Quantity
        Safety bounds for the valid temperature measurement range.

    Notes
    -----
    - The method returns temperature in Celsius (degC).
    - Uses natural logarithm via log1p(resistance.v).
    """

    def __init__(
        self,
        a,
        b,
        c,
        temperature_limit_min: Quantity,
        temperature_limit_max: Quantity,
    ):
        super().__init__(temperature_limit_min, temperature_limit_max)
        self.a = a
        self.b = b
        self.c = c

    def to_temperature(self, resistance: Quantity) -> Quantity:
        """
        Compute temperature from resistance using Steinhart–Hart equation.

        Steps
        -----
        1) Compute ln(R)
        2) Compute polynomial form a + b*ln(r) + c*(ln(r))^3
        3) Temperature = 1 / polynomial
        4) Convert to Kelvin and then to degC

        Returns
        -------
        Quantity
            Temperature in degrees Celsius.
        """
        validate_quantity(
            resistance,
            self.resistance_dimensionality,
            f"{type(self).__name__}.resistance",
            True,
        )

        # Perform logarithmic transformation
        lnohm = math.log1p(resistance.v)

        # Steinhart–Hart computation
        t1 = self.b * lnohm
        c2 = self.c * lnohm
        t2 = pow(c2, 3)
        temperature = 1 / (self.a + t1 + t2)

        # Convert from 1/K to K, then to Celsius
        temperature = temperature * Unit.K
        self.check_temperature_limits(temperature)
        return temperature.to("degC")