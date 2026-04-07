import enum
import socket
import logging
import time
from datetime import datetime
import xml.etree.cElementTree as xml
import pathlib
import traceback

from unitpy import Unit, Quantity
import numpy as np

from chembot.rabbitmq.messages import RabbitMessageAction
from chembot.configuration import config, create_folder
from chembot.equipment.sensors.sensor import Sensor
from chembot.utils.nmr_processing import nmr_check

logger = logging.getLogger(config.root_logger_name + ".nmr")


class MessageStates(str, enum.Enum):
    """
    High-level state labels seen in instrument status notifications.
    """
    Ready = "Ready"
    Running = "Running"
    Stopping = "Stopping"


class MessageState:
    """
    Parsed 'State' notification from the instrument.
    """
    def __init__(self, timestamp: str, protocol: str, status: str, data_folder: str):
        self.timestamp = timestamp  # e.g., "09:33:15"
        self.protocol = protocol    # e.g., "1D EXTENDED+"
        self.status = status        # e.g., "Ready"
        self.data_folder = data_folder


class MessageProgress:
    """
    Parsed 'Progress' notification from the instrument.
    """
    def __init__(self, timestamp: str, protocol: str, percentage: int, seconds_remaining: int):
        self.timestamp = timestamp          # e.g., "09:33:15"
        self.protocol = protocol            # e.g., "1D EXTENDED+"
        self.percentage = percentage        # integer percent progress
        self.seconds_remaining = seconds_remaining


class MessageError:
    """
    Parsed 'Error' notification from the instrument.
    """
    def __init__(self, timestamp: str, protocol: str, error: str):
        self.timestamp = timestamp  # e.g., "09:33:15"
        self.protocol = protocol    # e.g., "1D EXTENDED+"
        self.error = error          # error text


class MessageCompleted:
    """
    Parsed 'Completed' notification from the instrument.
    """
    def __init__(self, timestamp: str, protocol: str, completed: bool, successful: bool):
        self.timestamp = timestamp  # e.g., "09:33:15"
        self.protocol = protocol    # e.g., "1D EXTENDED+"
        self.completed = completed
        self.successful = successful


def parse_xml(xml_data: str):
    """
    Parse raw XML text from the instrument into a *Message* object.

    Parameters
    ----------
    xml_data : str
        XML message returned from the instrument (may contain escaped XML decl).

    Returns
    -------
    MessageState | MessageProgress | MessageError | MessageCompleted

    Raises
    ------
    ValueError
        If the payload does not match the expected schema.
    """
    # Some instrument messages include an escaped XML declaration; strip it if present
    xml_data = xml_data.replace('&lt;?xml version="1.0" encoding="utf-8"?&gt;', '')
    root = xml.fromstring(xml_data)

    if root.find('.//StatusNotification') is not None:
        timestamp = root.find('.//StatusNotification').get('timestamp')

        if root.find('.//State') is not None:
            protocol = root.find('.//State').get('protocol')
            status = root.find('.//State').get('status')
            data_folder = root.find('.//State').get('dataFolder')
            return MessageState(timestamp, protocol, status, data_folder)

        if root.find('.//Progress') is not None:
            protocol = root.find('.//Progress').get('protocol')
            percentage = int(root.find('.//Progress').get('percentage'))
            seconds_remaining = int(root.find('.//Progress').get('secondsRemaining'))
            return MessageProgress(timestamp, protocol, percentage, seconds_remaining)

        if root.find('.//Error') is not None:
            protocol = root.find('.//Error').get('protocol')
            error = root.find('.//Error').get('error')
            return MessageError(timestamp, protocol, error)

        if root.find('.//Completed') is not None:
            protocol = root.find('.//Completed').get('protocol')
            completed = root.find('.//Completed').get('completed')
            completed = True if completed == "true" else False
            successful = root.find('.//Completed').get('successful')
            successful = True if successful == "true" else False
            return MessageCompleted(timestamp, protocol, completed, successful)

    raise ValueError(f"Not recognized. \n{xml_data}")


class NMRSolvents(enum.Enum):
    """
    Enumerated list of common NMR solvents.
    """
    UNKNOWN = 0
    NONE = 1
    ACETONE = 2
    ACETONITRILE = 3
    BENZENE = 4
    CHLOROFORM = 5
    CYCLOHEXANE = 6
    DMSO = 7
    ETHANOL = 8
    METHANOL = 9
    PYRIDINE = 10
    TMS = 11
    THF = 12
    TOLUENE = 13
    TFA = 14
    WATER = 15
    OTHER = 16


class NMRScans(enum.Enum):
    """
    Typical scan-count presets for quick selection.
    """
    ONE = 1
    FOUR = 4
    EIGHT = 8
    SIXTEEN = 16
    THIRTYTWO = 32
    SIXTYFOUR = 64
    ONETWENTYEIGHT = 128
    TWOHOUNDREDFIFTYSIX = 256


class NMRAqTime(enum.Enum):
    """
    Acquisition times (s) presets.
    """
    POINTFOUR = 0.4
    POINTEIGHT = 0.8
    ONEPOINTSIX = 1.6
    THREEPOINTTWO = 3.2
    SIXPOINTFOUR = 6.4


class NMRRepTime(enum.Enum):
    """
    Repetition delays (s) presets between scans.
    """
    ONE = 1
    TWO = 2
    FOUR = 4
    SEVEN = 7
    TEN = 10
    FIFTEEN = 15
    THIRTY = 30
    SIXTY = 60
    ONETWENTHY = 120


class NMRPulseAngle(enum.Enum):
    """
    Pulse flip-angle presets (degrees).
    """
    THIRTY = 30
    FOURTYFIVE = 45
    SIXTY = 60
    NINTY = 90


class NMRComm:
    """
    Socket-based client for instrument control via a simple XML-over-TCP protocol.

    Purpose
    -------
    - Open/close TCP connections to the NMR controller.
    - Send XML command trees (e.g., Set/Start/CheckShim/QuickShim/PowerShim).
    - Receive asynchronous XML replies and interpret them (via `parse_xml`).

    Notes
    -----
    - `take_protron` name mirrors the original code (typo preserved intentionally).
    - Socket timeouts are set around acquisition (repetition time + 5 s) to bound waits.
    """

    def __init__(self, ip_address: str, port: int = 13000):
        self.ip_address = ip_address
        self.port = port
        self._connected = False
        self.socket: socket.socket = None

        self.open_connection()

    def __enter__(self):
        """Context manager enter: return self."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit: ensure connection is closed."""
        self.close_connection()

    @property
    def connected(self) -> bool:
        """Return current connection state flag."""
        return self._connected

    def open_connection(self):
        """
        Establish a TCP socket connection to the instrument.
        """
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect((self.ip_address, self.port))
        logger.info("Connection to NMR established.")
        self._connected = True

    def close_connection(self):
        """
        Close the TCP socket.
        """
        self.socket.close()
        logger.info("Connection to NMR closed.")
        self._connected = False

    def _send(self, message: xml.Element):
        """
        Serialize and send an XML message element over the socket.
        """
        message = xml.tostring(message, encoding="UTF-8")
        self.socket.send(message)

    def _look_for_reply(self):
        """
        Read messages until the socket raises (non-blocking loop with last chunks).

        Returns
        -------
        (chunk, old) : tuple[bytes, bytes]
            The last two received data blocks for logging/inspection.
        """
        # self.socket.settimeout(1.)
        chunk = ""
        old = ""
        try:
            while True:
                if chunk:
                    old = chunk
                chunk = self.socket.recv(8192)
                if chunk:
                    logger.info(chunk.decode())
        except socket.error as e:
            self.socket.settimeout(None)

        return chunk, old

    def stop(self):
        """
        Simple 'reset' pattern: close and reopen the socket.
        """
        self.close_connection()
        self.open_connection()

    # -------------------------
    # High-level "Set" commands
    # -------------------------

    def set_sample(self, name: str):
        """
        Set the sample name on the instrument.
        """
        message = xml.Element("Message")
        set_ = xml.SubElement(message, "Set")
        xml.SubElement(set_, "Sample").text = name

        self._send(message)
        logger.info(f"sample name set to: {name}")

    def set_solvent(self, solvent: NMRSolvents):
        """
        Set the solvent label on the instrument.
        """
        message = xml.Element("Message")
        set_ = xml.SubElement(message, "Set")
        xml.SubElement(set_, "Solvent").text = solvent.name

        self._send(message)
        logger.info(f"solvent set to: {solvent.name}")

    def set_folder(self, folder: str):
        """
        Set the data/user folder on the instrument.

        Notes
        -----
        - The current code assigns `folder = xml.SubElement(...)` which shadows
          the input string and then uses that variable in the next line.
          This mirrors the original logic **without modification**.
        """
        message = xml.Element("Message")
        set_ = xml.SubElement(message, "Set")
        folder = xml.SubElement(set_, "DataFolder")
        xml.SubElement(set_, "UserFolder").text = folder

        self._send(message)
        logger.info(f"folder set to: {folder}")

    # -------------------------
    # Acquisition and utilities
    # -------------------------

    def take_protron(
        self,
        scans: NMRScans = NMRScans.THIRTYTWO,
        aqtime: NMRAqTime = NMRAqTime.POINTEIGHT,
        reptime: NMRRepTime = NMRRepTime.ONE,
        pulse_angle: NMRPulseAngle = NMRPulseAngle.SIXTY
    ):
        """
        Start a 1D proton acquisition with a few key parameters.

        Parameters
        ----------
        scans : NMRScans
            Number of scans (averages).
        aqtime : NMRAqTime
            Acquisition time (s).
        reptime : NMRRepTime
            Repetition delay (s).
        pulse_angle : NMRPulseAngle
            Flip angle (degrees).

        Behavior
        --------
        - Builds XML with protocol '1D EXTENDED+' and Option tags for Number,
          AcquisitionTime, RepetitionTime, and PulseAngle.
        - Sets a socket timeout based on repetition time.
        - Waits for XML replies; parses each message with `parse_xml`.
        - Returns on a `MessageCompleted(successful=True)`. Logs and reduces
          an internal error counter on parsing issues but preserves behavior.
        """
        message = xml.Element("Message")
        start = xml.SubElement(message, "Start", protocol='1D EXTENDED+')
        xml.SubElement(start, "Option", name="Number", value=str(scans.value))
        xml.SubElement(start, "Option", name="AcquisitionTime", value=str(aqtime.value))
        xml.SubElement(start, "Option", name="RepetitionTime", value=str(reptime.value))
        xml.SubElement(start, "Option", name="PulseAngle", value=str(pulse_angle.value))

        self.socket.settimeout(reptime.value + 5)
        self._send(message)
        start = datetime.now()
        logger.info(f"proton started: {start}")
        try:
            _error_counter = 100
            while True:
                reply = self.socket.recv(8192)
                if reply:
                    try:
                        raw_message = reply.decode("utf-8")
                        if raw_message.count('&lt;?xml version="1.0" encoding="utf-8"?&gt;') > 1:
                            # If multiple messages are concatenated, split and keep message bodies
                            messages = raw_message.split('&lt;?xml version="1.0" encoding="utf-8"?&gt;')
                            messages = [message for i, message in enumerate(messages) if i % 2 == 1]
                        else:
                            messages = [raw_message]
                        for text in messages:
                            message = parse_xml(text)
                            if isinstance(message, MessageCompleted):
                                if message.successful:
                                    logger.info(
                                        f"proton completed at: {datetime.now()} ({(datetime.now() - start).total_seconds()} sec)"
                                    )
                                    return
                                raise ValueError("NMR not successful.")
                    except Exception as e:
                        logger.error("Issue on reply(inner)")
                        logger.error(traceback.format_exc())
                        if reply:
                            logger.error(reply.decode("utf-8"))
                        logger.error(e)
                        _error_counter -= 1
                        # if _error_counter == 0:
                        #     raise e

        except socket.error as e:
            logger.exception(f"Timeout during proton.")

        except Exception as e:
            logger.error("Issue NMR")
            if reply:
                logger.error(reply.decode("utf-8"))
            logger.error(e)

    def check_shim(self):
        """
        Send a 'CheckShimRequest' message and log the last two received chunks.
        """
        message = xml.Element("Message")
        xml.SubElement(message, "CheckShimRequest")

        self._send(message)
        chunk, old = self._look_for_reply()
        logger.info(f"chunk: {chunk}\nold: {old}")

    def quick_shim(self):
        """
        Send a 'QuickShimRequest' message and log the last two received chunks.
        """
        message = xml.Element("Message")
        xml.SubElement(message, "QuickShimRequest")

        self._send(message)
        chunk, old = self._look_for_reply()
        logger.info(f"chunk: {chunk}\nold: {old}")

    def power_shim(self):
        """
        Send a 'PowerShimRequest' message and log the last two received chunks.
        """
        message = xml.Element("Message")
        xml.SubElement(message, "PowerShimRequest")

        self._send(message)
        chunk, old = self._look_for_reply()
        logger.info(f"chunk: {chunk}\nold: {old}")


class NMR(Sensor):
    """
    Equipment wrapper for the NMR instrument, orchestrating valves, pumps, and acquisitions.

    Purpose
    -------
    - Encapsulates an `NMRComm` client (socket-based XML protocol).
    - Exposes a `write_measure` action that:
        * routes flow to the NMR via a valve,
        * computes a transport delay based on tubing volume and total flow,
        * moves back to waste,
        * pushes an additional plug with a pump,
        * attempts quick one-scan checks to detect signal, and
        * runs a higher-quality acquisition if a signal is detected.

    Notes
    -----
    - Returns placeholders (`0`) where future data integration is anticipated.
    - Uses RabbitMQ `RabbitMessageAction` to command associated devices (valves/pumps).
    """

    # Public access to enums for caller convenience
    SCANS = NMRScans
    SOLVENTS = NMRSolvents
    AQTIME = NMRAqTime
    REPTIME = NMRRepTime
    PULSEANGLE = NMRPulseAngle
    _method_path = str(pathlib.Path(__file__).parent)

    @property
    def _data_path(self):
        """
        Ensure and return the local data folder for NMR runs:
          <data_dir>/nmr
        """
        path = config.data_directory / pathlib.Path("nmr")
        create_folder(path)
        return path

    def __init__(self, name: str, ip_address: str, port: int):
        super().__init__(name)
        self._runner = NMRComm(ip_address, port)

    # -------------------------
    # Lifecycle hooks
    # -------------------------

    def _activate(self):
        """Activation hook (no-op; connection is opened in NMRComm.__init__)."""
        pass

    def _deactivate(self):
        """Deactivation hook: ensure socket connection is closed."""
        self._runner.close_connection()

    def _stop(self):
        """Emergency stop: cycle the socket to recover from transient faults."""
        self._runner.stop()

    def _write_measure(
        self,
        scans: NMRScans = NMRScans.THIRTYTWO,
        aqtime: NMRAqTime = NMRAqTime.POINTEIGHT,
        reptime: NMRRepTime = NMRRepTime.ONE,
        pulse_angle: NMRPulseAngle = NMRPulseAngle.SIXTY
    ) -> np.ndarray:
        """
        Lower-level measure call (skeleton): run a proton acquisition and return placeholder.

        Returns
        -------
        numpy.ndarray
            Placeholder (0 per original code). To be extended.
        """
        self._runner.take_protron(scans, aqtime, reptime, pulse_angle)
        return 0  # TODO

    def write_name(self, name: str):
        """
        Set the sample name on the instrument.
        """
        self._runner.set_sample(name)

    def write_measure(
        self,
        scans: NMRScans = NMRScans.EIGHT,
        aqtime: NMRAqTime = NMRAqTime.THREEPOINTTWO,
        reptime: NMRRepTime = NMRRepTime.FIFTEEN,
        pulse_angle: NMRPulseAngle = NMRPulseAngle.SIXTY,
        flow_rate: Quantity = None,
    ) -> np.ndarray:
        """
        Full orchestration to acquire an NMR spectrum during a flowing experiment.

        Steps
        -----
        1) **Route**: Move a valve to the NMR position, wait 2 s.
        2) **Transit delay**: Compute a plug travel time from tubing volume and *current* flow rates.
           - If no pump reports a flow (0), assume a small default (0.1 mL/min).
        3) **Route back**: Return valve to waste, wait 2 s.
        4) **Push plug**: Use pump five to infuse a defined plug (volume + rate), wait 60 s.
        5) **Signal check loop** (up to 5 attempts):
           - Set `temp_` name, run a quick 1‑scan to check for signal (via `nmr_check()`).
           - If signal is present → set final name, wait for repetition delay, acquire the full scan set.
           - If not → nudge the plug forward slightly and retry.
        6) Return placeholder array (per original behavior).

        Returns
        -------
        numpy.ndarray
            Placeholder (0 per original code). To be extended with actual data integration.
        """
        # switch valve to reactor → NMR
        self.rabbit.send(
            RabbitMessageAction("valve_five", self.name, "write_move", kwargs={"position": "NMR"})
        )
        time.sleep(2)

        # wait for plug: compute transit time (volume / flow_rate)
        vol = 3.14 * (6 * Unit.cm) * (0.03 * Unit.inch / 2) ** 2
        if flow_rate is None:
            flow_rate = 0 * Unit("ml/min")
            pumps = ["pump_one", "pump_two", "pump_three", "pump_four"]
            for pump in pumps:
                flow_rate += self.rabbit.send_and_consume(
                    RabbitMessageAction(pump, self.name, "read_flow_rate"), timeout=3, error_out=True
                ).value

            if flow_rate.v == 0:
                flow_rate = 0.1 * Unit("ml/min")
        time_ = vol / flow_rate
        time.sleep(time_.to("s").v)

        # switch valve back to waste
        self.rabbit.send(
            RabbitMessageAction("valve_five", self.name, "write_move", kwargs={"position": "waste"})
        )
        time.sleep(2)

        # push plug forward
        self.rabbit.send(
            RabbitMessageAction(
                "pump_five",
                self.name,
                "write_infuse",
                kwargs={"volume": 0.256 * Unit("ml"), "flow_rate": 0.263 * Unit("ml/min")}
            )
        )
        time.sleep(60)

        # take NMR (quick checks then final)
        for i in range(5):
            # quick 1-scan to see if signal present
            self.write_name("temp_")
            self._runner.take_protron(
                scans=NMRScans.ONE,
                aqtime=NMRAqTime.POINTEIGHT,
                reptime=NMRRepTime.ONE,
                pulse_angle=NMRPulseAngle.SIXTY
            )
            logger.info(f"NMR_start:{datetime.now().timestamp()}")

            if nmr_check(r"C:\Users\Robot2\Desktop\Dylan\NMR\Magritek\temp_"):
                # good signal → set final name and acquire
                self.write_name("DW2")
                time.sleep(reptime.value - 1)
                self._runner.take_protron(scans, aqtime, reptime, pulse_angle)
                break
            else:
                logger.info("No signal. Move and retry NMR")
                self.rabbit.send(
                    RabbitMessageAction(
                        "pump_five",
                        self.name,
                        "write_infuse",
                        kwargs={"volume": 0.005 * Unit.ml, "flow_rate": 0.15 * Unit("ml/min")}
                    )
                )
                time.sleep(10)
        else:
            logger.warning("No NMR signal found after 5 tries")

        return 0  # TODO