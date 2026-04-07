import abc
import logging

from chembot.configuration import config
from chembot.equipment.equipment import Equipment

logger = logging.getLogger(config.root_logger_name + ".sensor")


class Sensor(Equipment, abc.ABC):
    """
    Abstract base class for sensor devices in the Chembot system.

    Purpose
    -------
    - Provides a minimal, consistent interface for **measurement-capable devices**
      that plug into the `Equipment` lifecycle (activate/stop/deactivate) and
      your message-driven infrastructure.
    - Concrete subclasses implement hardware-specific behavior, most importantly
      the `write_measure()` action that triggers an acquisition and returns data.

    Integration
    -----------
    - Inherits from `Equipment`, so sensors automatically:
        * participate in activation/deactivation hooks,
        * expose standard state fields (e.g., STANDBY/RUNNING),
        * use shared logging and configuration patterns.
    - Typical subclasses:
        * Spectrometers (e.g., ATR-IR via OPUS/DDE),
        * Balances/Scales, Flow/Pressure sensors,
        * Cameras/Imagers that capture frames or spectra.

    Notes
    -----
    - Keep `write_measure()` side-effect free on configuration (only perform
      the measurement). Configuration (e.g., exposure time, averaging) is often
      better expressed as separate `write_*` actions to match your Equipment style.
    """

    def __init__(self, name: str):
        """
        Parameters
        ----------
        name : str
            Logical device name used for routing/logging/UI.
        """
        super().__init__(name)

    @abc.abstractmethod
    def write_measure(self):
        """
        Perform a measurement and return the acquired data.

        Returns
        -------
        Any
            Sensor-specific data payload (e.g., numpy array, scalar, dict).
            Subclasses should document the exact return type and structure.

        Notes
        -----
        - Implementations should handle hardware errors and raise meaningful
          exceptions (e.g., EquipmentError) so callers can react appropriately.
        - Consider updating relevant `self.update` fields (e.g., last timestamp,
          last value) and logging key metadata for traceability.
        """
        pass