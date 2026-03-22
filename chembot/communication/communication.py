import logging
import abc

from chembot.configuration import config
from chembot.equipment.equipment import Equipment

logger = logging.getLogger(config.root_logger_name + ".communication")


class Communication(Equipment, abc.ABC):
    """
    Abstract base class for communication endpoints (e.g., serial ports, TCP sockets, USB HID, etc.).

    This class provides:
      - Public, high-level read/write convenience methods with standardized logging.
      - Abstract, low-level methods (_write, _read, _read_until, _write_flush_buffer) that concrete
        subclasses must implement for a specific transport.

    Typical usage:
      - Subclass this and implement the underscored methods to bind to a specific medium (e.g., pyserial).
      - Use the public `write`, `read`, `read_until`, `write_plus_read`, `write_plus_read_until` in higher layers.
      - Lifecycle (`activate`/`deactivate`) is inherited from `Equipment`; `_stop` is intentionally minimal here.

    Note:
      - All public methods log actions via the configured application logger for observability.
      - The `name` attribute (inherited from `Equipment`) is used to tag log lines for this device.
    """

    def write(self, message: str):
        """
        Send a string message over the communication channel.

        Parameters
        ----------
        message : str
            The exact text to transmit (caller is responsible for appending terminators if the
            protocol requires them, e.g., '\n' for line-based devices).

        Behavior
        --------
        - Delegates to the subclass-implemented `_write`.
        - Emits a DEBUG log with the exact payload for traceability.
        """
        self._write(message)
        logger.debug(config.log_formatter(self, self.name, f"Action | write: {repr(message)}"))

    def read(self, read_bytes: int = 1) -> str:
        """
        Read a fixed number of bytes/characters from the communication channel.

        Parameters
        ----------
        read_bytes : int, default=1
            Number of bytes/characters to read (semantics depend on the underlying transport).

        Returns
        -------
        str
            The text received (subclasses may decode bytes to str if needed).

        Behavior
        --------
        - Delegates to `_read(read_bytes)`.
        - Logs the reply content at DEBUG level.
        """
        reply = self._read(read_bytes)
        logger.debug(config.log_formatter(self, self.name, f"Action | read: {repr(reply)}"))
        return reply

    def read_until(self, symbol: str = '\n') -> str:
        """
        Read from the communication channel until a terminator symbol is encountered.

        Parameters
        ----------
        symbol : str, default='\\n'
            The end-of-message delimiter to stop reading on.

        Returns
        -------
        str
            The received text with trailing CR/LF removed.

        Behavior
        --------
        - Delegates to `_read_until(symbol)`.
        - Strips trailing '\\n' and '\\r' for normalized results.
        - Logs the raw reply at DEBUG level.
        """
        reply = self._read_until(symbol)
        logger.debug(config.log_formatter(self, self.name, f"Action | read_until: {repr(reply)}"))
        # Normalize line endings so upstream logic doesn't have to.
        return reply.strip("\n").strip("\r")

    def write_flush_buffer(self):
        """
        Flush/clear the write buffer (and optionally hardware FIFOs) if the underlying
        transport supports/needs it (e.g., serial output buffer flush).

        Behavior
        --------
        - Delegates to `_write_flush_buffer()`.
        """
        self._write_flush_buffer()

    def write_plus_read_until(self, message: str, symbol: str = "\n") -> str:
        """
        Convenience method: write a message, then read until a terminator is seen.

        Equivalent to:
            self.write(message)
            return self.read_until(symbol)

        Parameters
        ----------
        message : str
            The text to transmit.
        symbol : str, default='\\n'
            The end-of-message delimiter for the subsequent read.

        Returns
        -------
        str
            The decoded/cleaned reply.
        """
        self.write(message)
        return self.read_until(symbol)

    def write_plus_read(self, message: str, read_bytes: int = 1) -> str:
        """
        Convenience method: write a message, then read a fixed number of bytes.

        Equivalent to:
            self.write(message)
            return self.read(read_bytes)

        Parameters
        ----------
        message : str
            The text to transmit.
        read_bytes : int, default=1
            Number of bytes/characters to read.

        Returns
        -------
        str
            The reply with the exact length requested (subject to transport semantics).
        """
        self.write(message)
        return self.read(read_bytes)

    # ---------- Abstract, transport-specific methods to implement in subclasses ----------

    @abc.abstractmethod
    def _write_flush_buffer(self):
        """
        Transport-specific write buffer flush.
        Implementations may:
          - flush a serial port TX buffer,
          - drain USB endpoint FIFOs,
          - no-op if not applicable.
        """
        ...

    @abc.abstractmethod
    def _write(self, message: str):
        """
        Transport-specific raw write operation.

        Implementations decide:
          - whether to encode str to bytes (e.g., UTF-8),
          - whether to append protocol terminators,
          - blocking vs non-blocking semantics,
          - error handling (raise vs retry).
        """
        ...

    @abc.abstractmethod
    def _read(self, bytes_: int) -> str:
        """
        Transport-specific fixed-length read.

        Parameters
        ----------
        bytes_ : int
            Number of bytes/characters to retrieve.

        Returns
        -------
        str
            The received data as a string. Implementations may convert bytes→str.
        """
        ...

    @abc.abstractmethod
    def _read_until(self, symbol: str = "\n") -> str:
        """
        Transport-specific read-until operation.

        Parameters
        ----------
        symbol : str, default='\\n'
            The terminator to stop reading.

        Returns
        -------
        str
            The raw text received (may include terminator; caller strips).
        """
        ...

    # ---------- Equipment lifecycle hook ----------

    def _stop(self):
        """
        Called during equipment shutdown.

        Intentionally left as a no-op here because the owning equipment/device typically
        manages connection teardown (e.g., closing a serial port) at a higher level.
        Subclasses may override if their transport requires explicit cleanup.
        """
        pass  # should be done by equipment using the communication port