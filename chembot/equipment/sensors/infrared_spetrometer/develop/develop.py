import win32ui          # required to initialise DDE on Windows via pywin32
import dde              # DDE (Dynamic Data Exchange) from pywin32
import numpy as np


class ExperimentRunner:
    """
    Minimal DDE client for Bruker OPUS to run ATR‑IR experiments and fetch results.

    Purpose
    -------
    - Open a DDE conversation with OPUS (Service="OPUS", Topic="System").
    - Send OPUS command strings to:
        * run background/reference scans,
        * run a sample measurement (with optional scan count and resolution),
        * read spectral data from a produced result file.
    - Parse line-based DDE replies and raise exceptions on non-OK responses.

    Notes
    -----
    - OPUS must be **running** on the machine for DDE to connect.
    - DDE reply text is normalized using an encode/decode step (UTF-16 LE roundtrip),
      matching OPUS’ string encoding behavior in practice.
    - `measure_sample()` assumes the **4th line** of the DDE reply contains the
      full path to the newly created result file (this is OPUS’s current behavior).
    """

    def __init__(self):
        # Create a DDE server "test" and attach a conversation to OPUS/System
        self.server = dde.CreateServer()
        self.server.Create("test")
        self.conversation = dde.CreateConversation(self.server)
        serverName = "OPUS"
        self.conversation.ConnectTo(serverName, "System")

        # Lightweight ping so we fail fast if conversation is unavailable
        self.conversation.Request("REQUEST_MODE")

    def request(self, command: str) -> list:
        """
        Send a DDE request to OPUS and split the multi-line reply.

        Parameters
        ----------
        command : str
            OPUS DDE command, e.g., "COMMAND_LINE MeasureSample (0,{...});"

        Returns
        -------
        list[str]
            Lines of the reply with the first element expected to be "OK".

        Raises
        ------
        Exception
            If the reply does not start with "OK" (full reply is included).
        """
        result = (
            self.conversation
            .Request(command)
            .encode("utf_16_le")
            .decode("utf_8")
            .splitlines()
        )
        if result[0] != "OK":
            raise Exception("\n".join(result))
        return result

    def run_background_scans(
        self,
        experiment_path: str,
        experiment_name: str,
        background_scans: int = None,
        resolution: float = None
    ):
        """
        Run background/reference scans using a given OPUS experiment.

        Parameters
        ----------
        experiment_path : str
            Filesystem path to the folder that contains the experiment (XPP/EXP).
        experiment_name : str
            The experiment name configured within OPUS (EXP).
        background_scans : int | None
            Number of background scans (defaults to the EXP setting if None).
        resolution : float | None
            Spectral resolution (defaults to the EXP setting if None).

        Raises
        ------
        Exception
            If inputs are invalid or OPUS returns a non-OK DDE response.
        """
        if background_scans is not None and background_scans <= 0:
            raise Exception("Number of background scans must be positive.")

        # Compose parameter string for OPUS
        parameters = "EXP='%s',XPP='%s'" % (experiment_name, experiment_path)
        if background_scans is not None:
            parameters += ",NSR=%d" % background_scans
        if resolution is not None:
            parameters += ",RES=%f" % resolution

        # COMMAND_LINE MeasureReference (mode, {params});
        self.request("COMMAND_LINE MeasureReference (0,{%s});" % parameters)

    def measure_sample(
        self,
        experiment_path: str,
        experiment_name: str,
        scans: int = None,
        resolution: float = None,
        measurement_display_mode: int = 0
    ) -> str:
        """
        Measure a sample using the given OPUS experiment, returning the result file path.

        The OPUS experiment defines defaults (scan count, resolution) that can be overridden
        by passing `scans` and/or `resolution`.

        Parameters
        ----------
        experiment_path : str
            Filesystem path to the folder that contains the experiment (XPP/EXP).
        experiment_name : str
            The experiment name configured within OPUS (EXP).
        scans : int | None
            Number of sample scans (defaults to EXP if None).
        resolution : float | None
            Spectral resolution (defaults to EXP if None). Must match background resolution.
        measurement_display_mode : int
            0 → start immediately (no user confirmation dialog),
            1 → show single scans until the user presses "Start Measurements" (undesirable here).

        Returns
        -------
        str
            Full path to the newly created result file (as returned by OPUS).

        Raises
        ------
        Exception
            If inputs are invalid or the reply format is unexpected.
        """
        if scans is not None and scans <= 0:
            raise Exception("Number of scans must be positive.")

        # Build parameter list: MDM (display mode), EXP (experiment name), XPP (path)
        parameters = "MDM=%d,EXP='%s',XPP='%s'" % (measurement_display_mode, experiment_name, experiment_path)
        if scans is not None:
            # NOTE: In some OPUS versions, NSR is also used. Here the code uses NSS.
            parameters += ",NSS=%d" % scans
        if resolution is not None:
            parameters += ",RES=%f" % resolution

        result = self.request("COMMAND_LINE MeasureSample (0,{%s});" % parameters)
        try:
            # Empirically, OPUS returns the result file path at index 3.
            return result[3]
        except Exception:
            raise Exception("Unexpected data format. OPUS returned: " + "\n".join(result))

    def get_results(self, result_file: str) -> np.array:
        """
        Read spectral data from a result file via OPUS DDE.

        Workflow
        --------
        1) `READ_FROM_FILE <path>`   → select the file
        2) `READ_FROM_BLOCK AB`      → choose the absorbance block (AB)
        3) `DATA_VALUES`             → request metadata (N, high/low wavenumbers, scaling)
        4) `READ_DATA`               → read the actual data lines

        Returns
        -------
        numpy.ndarray
            2D array with shape (N, 2): [wavenumber, value].

        Raises
        ------
        Exception
            If the reply is malformed/unexpected.
        """
        result_read = self.request("READ_FROM_FILE %s" % result_file)
        result_block = self.request("READ_FROM_BLOCK AB")
        result_data_points = self.request("DATA_VALUES")
        result_data = self.request("READ_DATA")
        try:
            data_length = int(result_data[2])
            wavenumber_upper = float(result_data[3])
            wavenumber_lower = float(result_data[4])
            scaling_factor = int(result_data[5])

            # Reconstruct x-axis linearly from lower..upper across N points.
            # Values are reported line-by-line starting at index 6.
            data = np.array([
                [
                    wavenumber_lower + i * (wavenumber_upper - wavenumber_lower) / (data_length - 1),
                    float(result_data[6 + i]),
                ]
                for i in range(data_length)
            ])
            return data
        except Exception:
            raise Exception("Unexpected data format. OPUS returned: " + "\n".join(result))


# -------------------------
# Simple imperative demo
# -------------------------
# NOTE: This bottom section is an example script that:
#   1) Runs background scans,
#   2) Waits for user input,
#   3) Runs a single measurement,
#   4) Reads the spectrum,
#   5) Writes a "signal.txt" with 2 columns (wavenumber, value),
#   6) Attempts to plot with matplotlib (imports not shown in original).
#
# If you want to keep it runnable as-is, consider guarding with `if __name__ == "__main__":`
# and adding the required `matplotlib.pyplot as plt` import. Preserving original structure below.

er = ExperimentRunner()
path = "C:\\Users\\Robot2\\Desktop\\test"
name = "ATR_DI"

# 1) Run a single background scan
er.run_background_scans(path, name, 1)

# 2) Wait for user prompt to continue
input("Press enter to run measurements.")

# 3) Run a single-scan sample measurement and get the result file path
rf = er.measure_sample(path, name, 1)
print(rf)

# 4) Read the (wavenumber, value) data from the result file
res = er.get_results(rf)
print(res)

# 5) Persist to a simple two-column text file
with open("signal.txt", "w") as f:
    for i in range(len(res)):
        f.write("%f %f\n" % (res[i, 0], res[i, 1]))

# 6) Plot (optional). The original snippet references plt without import;
#    ensure `import matplotlib.pyplot as plt` exists in your environment.
# import matplotlib.pyplot as plt
# plt.plot(res[:, 0], res[:, 1])
# plt.xlabel("Wavenumber")
# plt.ylabel("Absorbance (AU)")
# plt.title("ATR-IR Spectrum")
# plt.gca().invert_xaxis()  # common convention for IR plots
# plt.show()