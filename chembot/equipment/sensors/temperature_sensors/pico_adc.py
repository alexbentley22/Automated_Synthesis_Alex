import logging
import pathlib

from unitpy import Quantity

from chembot.reference_data.pico_pins import PicoHardware
from chembot.configuration import config, create_folder
from chembot.rabbitmq.messages import RabbitMessageAction
from chembot.communication.serial_pico import PicoSerial
from chembot.equipment.sensors.sensor import Sensor
from chembot.equipment.sensors.temperature_sensors.calibration import ThermalCalibration


logger = logging.getLogger(config.root_logger_name + ".temperature")


def analog_to_volt(num: int, supply_volt: Quantity, resolution: int) -> Quantity:
    """
    Convert a raw ADC integer reading into a voltage.

    Parameters
    ----------
    num : int
        ADC sample (integer from 0..resolution).
    supply_volt : Quantity
        ADC reference or supply voltage (e.g., Pico v_sys).
    resolution : int
        ADC digital resolution (e.g., 2^16 for a 16-bit ADC).

    Returns
    -------
    Quantity
        Voltage corresponding to the ADC reading.
    """
    return num * supply_volt / resolution


def voltage_divider_voltage_to_ohm(
        voltage: Quantity,
        resistor: Quantity,
        resistor_order: bool,
        supply_voltage: Quantity
) -> Quantity:
    """
    Solve the resistance of a thermistor in a simple voltage-divider circuit.

    Circuit (high-level)
    --------------------
        V_in → R1 → (V_out) → R2 → ground

    Parameters
    ----------
    voltage : Quantity
        Measured V_out from the ADC.
    resistor : Quantity
        Known reference resistor (R1 or R2 depending on orientation).
    resistor_order : bool
        If True  → solve for R1 (thermistor is R1).
        If False → solve for R2 (thermistor is R2).
    supply_voltage : Quantity
        Voltage feeding the divider (usually Pico's v_sys).

    Returns
    -------
    Quantity
        Computed resistance of the unknown leg.
    """
    if resistor_order:  # solving for R1
        return resistor * ((supply_voltage / voltage).v - 1)

    # solving for R2
    return resistor / ((supply_voltage / voltage).v - 1)


class TemperatureProbePicoADC(Sensor):
    """
    Temperature sensor interface using a Pico ADC and a voltage divider.

    Purpose
    -------
    - Read raw ADC values from a Raspberry Pi Pico (via RabbitMQ → PicoSerial).
    - Convert ADC readings to voltage.
    - Convert voltage to resistance using the known divider resistor.
    - Convert resistance to temperature using a provided `ThermalCalibration` model.

    Notes
    -----
    - Communication with the Pico is asynchronous through RabbitMQ.
    - The actual ADC sampling command (PicoSerial.write_serial) and its arguments
      must be provided by upstream logic (currently 'FIX ME').
    """

    @property
    def _data_path(self):
        """
        Directory for storing temperature data:
            <data_directory>/temperature
        """
        path = config.data_directory / pathlib.Path("temperature")
        create_folder(path)
        return path

    """ Voltage Divider setup """
    def __init__(
            self,
            name: str,
            communication: str,
            calibration: ThermalCalibration,
            resistor: Quantity,
            resistor_order: bool,
            reference_voltage: Quantity = PicoHardware.v_sys,
    ):
        """
        Parameters
        ----------
        name : str
            Device name used by RabbitMQ.
        communication : str
            Name of the Pico communication channel (RabbitMQ destination).
        calibration : ThermalCalibration
            Object used to convert resistance → temperature.
        resistor : Quantity
            Known fixed resistor in the divider.
        resistor_order : bool
            True  → R_thermistor is the high-side resistor (R1).
            False → R_thermistor is low-side (R2).
        reference_voltage : Quantity, optional
            ADC reference / supply voltage (defaults to Pico v_sys).
        """
        super().__init__(name)
        self.communication = communication
        self.calibration = calibration
        self.resistor = resistor
        self.resistor_order = resistor_order
        self.reference_voltage = reference_voltage

        # Default Pico internal temperature pin
        self._pin = PicoHardware.pin_internal_temp

    def _activate(self):
        """Activation hook — currently unused."""
        pass

    def _deactivate(self):
        """Deactivation hook — currently unused."""
        pass

    def _stop(self):
        """Emergency stop hook — currently unused."""
        pass

    def write_measure(self) -> Quantity:
        """
        Perform a single temperature measurement.

        Workflow
        --------
        1. Send a RabbitMQ message asking the Pico to sample the ADC.
        2. Wait for the reply (blocking).
        3. Convert ADC integer → voltage.
        4. Convert voltage → thermistor resistance.
        5. Convert resistance → temperature using the loaded calibration model.

        Returns
        -------
        Quantity
            Temperature returned by calibration model.
        """
        message = RabbitMessageAction(
            self.communication,
            self.name,
            PicoSerial.write_serial,
            kwargs="FIX ME",  # placeholder - upstream control must provide real command
        )

        reply = self.rabbit.send_and_consume(message, error_out=True)

        adc_reading = reply.value
        voltage = analog_to_volt(adc_reading, self.reference_voltage, PicoHardware.ADC_resolution)
        resistance = voltage_divider_voltage_to_ohm(
            voltage, self.resistor, self.resistor_order, self.reference_voltage
        )

        return self.calibration.to_temperature(resistance)