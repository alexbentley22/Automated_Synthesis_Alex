import pathlib
import logging
import queue
import threading

import win32ui  # needed to locate/initialize the DDE subsystem from pywin32
import dde      # DDE (Dynamic Data Exchange) from the pywin32 package
import numpy as np

from chembot.configuration import config, create_folder
from chembot.equipment.sensors.sensor import Sensor

logger = logging.getLogger(config.root_logger_name + ".atir")


class ATIRRunner:
    """
    Thin wrapper around a DDE conversation with Bruker OPUS for ATR-IR measurements.

    Purpose
    -------
    - Starts a DDE **server** and opens a **conversation** with OPUS
      (Service="OPUS", Topic="System").
    - Issues OPUS DDE **command strings** and parses **line-based** replies.
    - Provides two high-level actions:
        * `run_background_scans(...)`
        * `measure_sample(...)`
      and a data accessor:
        * `get_results(result_file)` → (wavenumber, absorbance) columns.

    Notes
    -----
    - OPUS must be **running** on the machine for DDE to connect.
    - Replies are expected to begin with `"OK"` on success; otherwise an exception is raised.
    - The return format of `measure_sample` assumes the **4th line** of the reply contains
      the **full path** to the generated spectrum file (as per current OPUS DDE behavior).
    """

    def __init__(self):
        # Create a DDE server and connect to OPUS "System"
        self.server = dde.CreateServer()
        self.server.Create("test")  # server (arbitrary) name for this client
        self.conversation = dde.CreateConversation(self.server)
        self.conversation.ConnectTo("OPUS", "System")

        # Touch the conversation to confirm basic communication
        self.conversation.Request("REQUEST_MODE")
        logger.debug(config.log_formatter(ATIRRunner, "atirrunner", "DDE server activated"))

    def request(self, command: str) -> list:
        """
        Send a DDE request to OPUS and parse the multi-line reply.

        Parameters
        ----------
        command : str
            OPUS DDE command string (e.g., "COMMAND_LINE MeasureSample (...);").

        Returns
        -------
        list[str]
            Lines of the reply, with leading "OK" on success.

        Raises
        ------
        Exception
            If the reply does not start with "OK".
        """
        logger.debug(f"commands: {command}")

        # OPUS DDE returns a bytes-like structure; the encode/decode dance below
        # aligns with the expected string format from OPUS (UTF-16 LE in practice).
        result = (
            self.conversation.Request(command)
            .encode("utf_16_le")
            .decode("utf_8")
            .splitlines()
        )
        if result[0] != "OK":
            # Surface the entire returned message for troubleshooting
            raise Exception("\n".join(result))
        return result

    def run_background_scans(
        self,
        experiment_path: str,
        experiment_name: str,
        scans: int = None,
        resolution: float = None,
    ):
        """
        Run background/reference scans for a given OPUS experiment.

        Parameters
        ----------
        experiment_path : str
            Filesystem path containing the experiment file (XPP).
        experiment_name : str
            The experiment name (EXP) configured within OPUS.
        scans : int | None
            Number of background scans to perform (defaults to EXP setting).
        resolution : float | None
            Spectral resolution to use (defaults to EXP setting).

        Raises
        ------
        Exception
            If invalid inputs or a non-OK DDE reply occurs.
        """
        if scans is not None and scans <= 0:
            raise Exception("Number of background scans must be positive.")

        # Build parameter list for OPUS command
        parameters = f"EXP='{experiment_name}',XPP='{experiment_path}'"
        if scans is not None:
            parameters += f",NSR={scans}"
        if resolution is not None:
            parameters += f",RES={resolution}"

        # COMMAND_LINE MeasureReference (mode, {params});
        self.request("COMMAND_LINE MeasureReference (0,{" + parameters + "});")

    def measure_sample(
        self,
        experiment_path: str,
        experiment_name: str,
        scans: int = None,
        resolution: float = None,
    ) -> str:
        """
        Trigger a sample measurement via OPUS and return the resulting file path.

        The experiment (EXP/XPP) provides defaults; `scans` and `resolution`
        can override those settings at runtime.

        Parameters
        ----------
        experiment_path : str
            Filesystem path containing the experiment file (XPP).
        experiment_name : str
            The experiment name (EXP) configured within OPUS.
        scans : int | None
            Number of scans (defaults to EXP).
        resolution : float | None
            Resolution (defaults to EXP).

        Returns
        -------
        str
            Full path to the generated measurement result file (as returned by OPUS).

        Raises
        ------
        Exception
            If inputs are invalid or the reply format is unexpected.
        """
        # Display mode: 0 = start immediately (no manual confirmation prompt)
        #               1 = show single scans until "Start Measurements" is pressed (not desired here)
        measurement_display_mode = 0

        if scans is not None and scans <= 0:
            raise Exception("Number of scans must be positive.")

        # Assemble command-line parameters for OPUS
        parameters = f"MDM={measurement_display_mode},EXP='{experiment_name}',XPP='{experiment_path}'"
        if scans is not None:
            parameters += f",NSR={scans}"
        if resolution is not None:
            parameters += f",RES={resolution}"

        # Optional sample name (SNM) set here for traceability
        parameters += f",SNM=RAFT2_3"

        # COMMAND_LINE MeasureSample (mode, {params});
        result = self.request("COMMAND_LINE MeasureSample (0,{" + parameters + "});")
        logger.debug("result: " + str(result))
        try:
            # Empirically, OPUS returns the file path on the 4th line
            return result[3]
        except Exception:
            raise Exception("Unexpected data format. OPUS returned: " + "\n".join(result))

    def get_results(self, result_file: str) -> np.array:
        """
        Load spectrum data (wavenumber, values) for the specified result file via DDE.

        Workflow
        --------
        1) `READ_FROM_FILE <path>`  → select the file
        2) `READ_FROM_BLOCK AB`     → select the AB (absorbance) block (or other block as needed)
        3) `DATA_VALUES`            → request metadata
        4) `READ_DATA`              → retrieve the data lines

        Returns
        -------
        numpy.ndarray
            Two-column array: [wavenumber, value] with shape (N, 2).

        Raises
        ------
        ValueError
            If OPUS indicates an error or the reply is malformed.
        """
        _ = self.request("READ_FROM_FILE %s" % result_file)  # Ensure correct file selected
        _ = self.request("READ_FROM_BLOCK AB")               # Target the absorbance block
        _ = self.request("DATA_VALUES")                      # Request metadata
        result_data = self.request("READ_DATA")              # Read the numeric data

        # Basic reply validation
        status = result_data[0]
        if status != "OK":
            raise ValueError("Error with reading data from AT-IR.")

        # Parse header values
        data_length = int(result_data[2])
        wavenumber_upper = float(result_data[3])
        wavenumber_lower = float(result_data[4])
        scaling_factor = int(result_data[5])

        # Build x-axis (wavenumber) from lower → upper with N points
        x = np.linspace(wavenumber_lower, wavenumber_upper, data_length)

        # Data payload is line 6 .. second-to-last; apply integer scaling (OPUS convention)
        y = np.array(result_data[6:-1], dtype="float64") * scaling_factor

        return np.column_stack((x, y))


class ATIR(Sensor):
    """
    Equipment wrapper for the ATR-IR sensor using OPUS DDE (Bruker).

    Purpose
    -------
    - Integrates OPUS control (`ATIRRunner`) into your Chembot `Sensor` device.
    - Persists data to a **dated** `data/atir` directory (see `config.data_directory`).
    - Exposes `write_measure(...)` and `write_background(...)` actions for orchestration.

    Notes
    -----
    - `_method_name` and `_method_path` point to the default EXP/XPP resources
      used when issuing measurements.
    - Activation/stop hooks are stubs here and can be extended for lifecycle handling.
    """

    # Default OPUS experiment info (adjust to your deployment)
    _method_name = "ATR_DI2"
    _method_path = str(pathlib.Path(__file__).parent)

    @property
    def _data_path(self):
        """
        Ensure and return the local data folder for ATR-IR runs:
          <data_dir>/atir
        """
        path = config.data_directory / pathlib.Path("atir")
        create_folder(path)
        return path

    def __init__(self, name: str):
        super().__init__(name)
        self._runner = ATIRRunner()

    # -------------
    # Lifecycle stubs
    # -------------

    def _activate(self):
        """Device activation hook (no-op; OPUS DDE is established in ATIRRunner.__init__)."""
        pass

    def _deactivate(self):
        """Device deactivation hook (no-op; extend if you need teardown behavior)."""
        pass

    def _stop(self):
        """Emergency stop hook (no-op; extend to abort in-flight DDE commands if required)."""
        pass

    # -------------
    # Actions
    # -------------

    def write_measure(self, data_name: str = None, scans: int = 16) -> np.ndarray:
        """
        Trigger a sample measurement and return the **absorbance** vector.

        Parameters
        ----------
        data_name : str | None
            Optional sample identifier (currently unused; SNM is set in runner).
        scans : int
            Number of sample scans to acquire (default 16).

        Returns
        -------
        numpy.ndarray
            1-D array of absorbance values (y), extracted from the (x,y) matrix.
            Note: wavelength/wavenumber axis is not returned here.
        """
        rf = self._runner.measure_sample(self._method_path, self._method_name, scans)
        logger.warning(rf)
        # TODO: Revisit to also return the wavenumber axis and/or save to disk.
        return self._runner.get_results(rf)[:, 1]  # y-values only

    def write_background(self, scans: int = 16):
        """
        Run background scans for the configured method.

        Parameters
        ----------
        scans : int
            Number of background/reference scans (default 16).
        """
        self._runner.run_background_scans(self._method_path, self._method_name, scans)