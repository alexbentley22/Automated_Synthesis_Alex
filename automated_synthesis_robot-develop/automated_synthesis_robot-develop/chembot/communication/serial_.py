import logging
import time

import serial
from serial.tools.list_ports import comports

from chembot.configuration import config
from chembot.communication.communication import Communication

logger = logging.getLogger(config.root_logger_name + ".communication")


class Serial(Communication):
    """
    Concrete communication endpoint that uses a local serial (COM) port via pySerial.

    Responsibilities
    ----------------
    - Open/close and manage a serial port connection.
    - Implement the low-level read/write methods required by the Communication base class.
    - Provide convenience helpers for buffer inspection and write-then-read patterns.

    Notes
    -----
    - Encoding/decoding uses `config.encoding` (e.g., "utf-8") to convert between str and bytes.
    - The actual high-level protocol (terminators, commands, etc.) is handled by callers.
    - Device lifecycle hooks `_activate()` and `_deactivate()` integrate with the Equipment base class.
    """

    # Snapshot of available ports at import time (useful for quick validation)
    # If you hot-plug devices after import, consider re-querying `comports()` dynamically.
    available_ports = [port.device for port in comports()]

    def __init__(
        self,
        name: str,
        port: str,
        baud_rate: int = 9600,
        parity: str = 'N',
        stop_bits: int = 1,
        bytes_: int = 8,
        timeout: float = 10,
    ):
        """
        Create and open a serial connection.

        Parameters
        ----------
        name : str
            Logical name of this communication endpoint (used for logging/routing).
        port : str
            OS device path / name for the serial port (e.g., "COM5" on Windows, "/dev/ttyUSB0" on Linux).
        baud_rate : int, default=9600
            Baud rate for the serial connection.
        parity : {'N','E','O','M','S'}, default='N'
            Parity setting: None, Even, Odd, Mark, Space.
        stop_bits : {1, 1.5, 2}, default=1
            Number of stop bits.
        bytes_ : {7, 8, 9}, default=8
            Data bits per character.
        timeout : float, default=10
            Read timeout in seconds (pySerial read operations will block up to this duration).

        Behavior
        --------
        - Validates that the specified `port` appears in `available_ports`.
        - Opens the serial port immediately with the provided settings.
        - Stores configuration for observability and later inspection.
        """
        super().__init__(name)
        self.available_port(port)  # Validate the port is currently visible to the OS.

        # Open the port with pySerial. Note: pySerial may raise if the port is busy or absent.
        self.serial = serial.Serial(
            port=port,
            baudrate=baud_rate,
            stopbits=stop_bits,
            bytesize=bytes_,
            parity=parity,
            timeout=timeout,
        )

        # Persist configuration (also added to self.attrs for structured logging/inspection)
        self.port = port
        self.baud_rate = baud_rate
        self.stop_bits = stop_bits
        self.bytes_ = bytes_
        self.parity = parity
        self.timeout = timeout

        # Attributes to be exposed by Equipment's standard introspection/logging mechanisms
        self.attrs += ['port', "baud_rate", "stop_bits", "bytes_", "parity", "timeout"]

    def __repr__(self):
        """
        Human-readable representation that shows the logical name and bound OS port.
        """
        return self.name + f" || port: {self.serial.port}"

    # ---------- Equipment lifecycle hooks ----------

    def _activate(self):
        """
        Called when the Equipment lifecycle transitions to 'active'.

        Behavior
        --------
        - Flush input/output buffers to ensure we start with a clean channel
          (no stale reads/writes).
        """
        self._write_flush_buffer()

    def _deactivate(self):
        """
        Called when the Equipment lifecycle transitions to 'inactive'.

        Behavior
        --------
        - Closes the serial port. Subsequent reads/writes will fail unless reactivated/opened.
        """
        self.serial.close()

    # ---------- Communication abstract implementations ----------

    def _write_flush_buffer(self):
        """
        Clear both input and output buffers on the serial port.

        Use this to discard any partially received frames or queued transmissions.
        """
        self.serial.flushInput()
        self.serial.flushOutput()

    def _write(self, message: str):
        """
        Transport-specific raw write (str -> bytes).

        Parameters
        ----------
        message : str
            Payload to send. This method encodes using `config.encoding`.

        Notes
        -----
        - No terminator is appended automatically. Callers should include e.g., '\n' if required.
        """
        self.serial.write(message.encode(config.encoding))

    def _read(self, read_bytes: int) -> str:
        """
        Transport-specific fixed-length read.

        Parameters
        ----------
        read_bytes : int
            Number of bytes to read from the port. May return fewer than requested if timeout occurs.

        Returns
        -------
        str
            The decoded bytes as a string (using `config.encoding`).
        """
        return self.serial.read(read_bytes).decode(config.encoding)

    def _read_until(self, symbol: str = "\n") -> str:
        """
        Transport-specific read-until operation.

        Parameters
        ----------
        symbol : str, default='\\n'
            Terminator that indicates the end of a frame/line.

        Returns
        -------
        str
            The decoded line including the terminator (caller strips CR/LF in the base class).
        """
        return self.serial.read_until(symbol.encode(config.encoding)).decode(config.encoding)

    # ---------- Additional helpers for diagnostics/inspection ----------

    def read_port(self) -> str:
        """
        Return the OS device path/name of the currently open port.
        """
        return self.serial.port

    def read_parity(self) -> str:
        """
        Return the parity setting.

        Possible values
        ---------------
        'N' (none), 'E' (even), 'O' (odd), 'M' (mark), 'S' (space)
        """
        return self.serial.parity

    def read_stop_bits(self) -> int:
        """
        Return the number of stop bits.

        Possible values
        ---------------
        1, 1.5, 2
        """
        return self.serial.stopbits

    def read_bytes(self) -> int:
        """
        Return the number of data bits per character.

        Typical values
        --------------
        7, 8, or 9
        """
        return self.serial.bytesize

    def read_baudrate(self) -> str:
        """
        Return the configured baud rate.
        """
        return self.serial.baudrate

    def read_bytes_in_buffer_in(self) -> int:
        """
        Return the number of bytes currently available in the input buffer (RX).
        """
        return self.serial.in_waiting

    def read_bytes_in_buffer_out(self) -> int:
        """
        Return the number of bytes currently queued in the output buffer (TX).
        """
        return self.serial.out_waiting

    def read_all_buffer(self) -> str:
        """
        Read and return all bytes currently in the input buffer (RX).

        Notes
        -----
        - This reads exactly `in_waiting` bytes at the moment of the call.
        - Useful for draining/inspecting accumulated data after a delay.
        """
        return self.read(self.serial.in_waiting)

    def write_plus_read_all_buffer(self, message: str, delay: float = 0.2) -> str:
        """
        Write a message, wait briefly, then read all bytes accumulated in the RX buffer.

        Parameters
        ----------
        message : str
            Payload to transmit (caller provides terminator if required).
        delay : float, default=0.2
            Time to sleep (seconds) before reading, allowing the device to respond.

        Returns
        -------
        str
            All data currently available in the RX buffer after the delay.

        Notes
        -----
        - This is a pragmatic helper for simple request/response devices that don't
          have a strict delimiter or known response length.
        """
        self.write(message)
        time.sleep(delay)  # allow time for the device to respond before draining the buffer
        return self.read_all_buffer()

    # ---------- Validation ----------

    @classmethod
    def available_port(cls, port: str):
        """
        Validate that the given OS port string is currently available to the computer.

        Parameters
        ----------
        port : str
            Port path/name to validate (e.g., 'COM5', '/dev/ttyUSB0').

        Raises
        ------
        ValueError
            If the specified port does not appear in the snapshot `available_ports`.

        Notes
        -----
        - This uses a static snapshot taken at import time. If devices are hot-plugged
          after import, you may want to re-check with `port in [p.device for p in comports()]`
          at call time instead of using this cached class attribute.
        """
        if port not in cls.available_ports:
            raise ValueError(f"Port '{port}' is not connected to computer.")