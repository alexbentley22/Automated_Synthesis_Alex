import enum
import logging
import time

from unitpy import Quantity, Unit

from chembot.configuration import config
from chembot.communication.serial_ import Serial
from chembot.reference_data.pico_pins import PicoHardware

logger = logging.getLogger(config.root_logger_name + ".communication")


def encode_message(text: str):
    """
    Encode a message before sending to the Pico.

    Replaces newline characters with placeholders so the on-wire protocol
    can remain safely line-delimited by '\n' without embedding raw CR/LF.

    Parameters
    ----------
    text : str
        The raw application message.

    Returns
    -------
    str
        Encoded message where '\n' -> '__n__' and '\r' -> '__r__'.
    """
    return text.replace("\n", "__n__").replace("\r", "__r__")


def decode_message(text: str):
    """
    Decode a received message from the Pico.

    Reverses the placeholder substitutions performed during encode.

    Parameters
    ----------
    text : str
        The wire-level message payload with placeholders.

    Returns
    -------
    str
        Decoded message where '__n__' -> '\n' and '__r__' -> '\r'.
    """
    return text.replace("__n__", "\n").replace("__r__", "\r")


def analog_to_voltage(analog: int) -> Quantity:
    """
    Convert a 16-bit ADC reading to an absolute voltage.

    Assumes a 16-bit ADC range (0..65535) and uses `PicoHardware.v_sys`
    as the reference supply.

    Parameters
    ----------
    analog : int
        Raw ADC code (0..65535).

    Returns
    -------
    Quantity
        Voltage as a unit-aware quantity.
    """
    return analog * PicoHardware.v_sys / 65535   # 65535 = 2**16 or 16 bit resolution of the ADC


def analog_to_temperature(analog: int) -> Quantity:
    """
    Convert a 16-bit ADC reading to the Pico RP2040 internal temperature (°C).

    Uses the datasheet equation (RP2040 section 4.9.5):
        T(°C) = 27 - (V_sense - 0.706) / 0.001721

    Parameters
    ----------
    analog : int
        Raw ADC code (0..65535) from the internal temperature channel.

    Returns
    -------
    Quantity
        Temperature in degrees Celsius (unit-aware).
    """
    voltage = analog_to_voltage(analog)
    return (27 - (voltage.v - 0.706)/0.001721) * Unit.degC  # equation from RP2040 data sheet (section 4.9.5)


class PinStatus(enum.Enum):
    """
    Local bookkeeping for how a GPIO pin is currently being used.

    This is not enforced by hardware; it's a host-side state mirror that helps
    avoid accidental mode conflicts and improves observability.
    """
    STANDBY = -1
    DIGITAL_OFF = 0  # output pull down
    DIGITAL_ON = 1
    DIGITAL_INPUT = 2
    ANALOG = 3
    PWM = 4
    SERIAL = 5
    SPI = 6
    I2C = 7


class PicoSerial(Serial):
    """
    Host-side driver for a Raspberry Pi Pico running a simple ASCII command firmware.

    This class:
      - Extends the generic `Serial` communication class with Pico-specific protocol helpers.
      - Encodes/decodes payloads to ensure '\n' and '\r' are not embedded in-line.
      - Wraps common Pico actions (GPIO digital, ADC, PWM, UART, SPI, I2C) with validation.
      - Keeps a host-side map of pin modes (`self.pins`) for easier debugging/inspection.

    Protocol overview
    -----------------
    - All commands are line-based and end with '\n'.
    - Replies are a single token or CSV, depending on the command.
    - Methods below perform basic argument validation via `PicoHardware` helpers
      and raise `ValueError` if the Pico's reply is unexpected.
    """

    def __init__(self,
                 name: str,
                 port: str,
                 ):
        """
        Create a Pico serial endpoint.

        Parameters
        ----------
        name : str
            Logical name used for logging and resource lookup.
        port : str
            OS serial device (e.g., 'COM5', '/dev/ttyACM0').
        """
        super().__init__(name, port)
        # Initialize all GPIO pins to STANDBY in the host-side mirror
        self.pins = {pin: PinStatus.STANDBY for pin in PicoHardware.pins_GPIO}
        # Filled on activation via 'v' handshake
        self.pico_version = None

        self.attrs += ["pico_version"]

    def __repr__(self):
        """
        Human-readable identifier including port path.
        """
        return self.name + f" || port: {self.port}"

    def _activate(self):
        """
        Equipment lifecycle hook: clear buffers and retrieve firmware version.

        Behavior
        --------
        - Flush RX/TX buffers (via parent _activate()).
        - Send 'v' to read the Pico firmware version string.
        - Store the version (characters after the leading 'v').
        """
        super()._activate()
        reply = self.write_plus_read_until("v")
        if reply[0] != "v":
            raise ValueError(f"Unexpected reply from Pico during activation.\n reply:{reply}")
        self.pico_version = reply[1:]

    def _deactivate(self):
        """
        Equipment lifecycle hook: attempt to reset pins, then close port.

        Behavior
        --------
        - Send 'r' to reset all pins on the Pico.
        - Close the serial connection via parent.
        """
        self.write_reset()
        super()._deactivate()

    def _write(self, message: str):
        """
        Transport-specific send with protocol encoding and terminator.

        Encodes message to escape CR/LF and appends a single '\n' to delimit frames.
        """
        message = encode_message(message) + "\n"
        super()._write(message)

    def _read(self, read_bytes: int) -> str:
        """
        Transport-specific fixed-length read with protocol decoding.

        Strips trailing newline and decodes CR/LF placeholders back to literal characters.
        """
        message = super()._read(read_bytes)
        return decode_message(message.strip("\n"))

    def _stop(self):
        """
        Equipment lifecycle hook on stop: issue Pico reset command.

        Sends 'r' (reset). Errors will propagate if the Pico returns an unexpected token.
        """
        self.write_plus_read_until("r")

    def read_pico_version(self) -> str:
        """
        Return the Pico firmware version string (set during activation).
        """
        return self.pico_version

    def write_reset(self):
        """
        Reset all pins on the Pico to their default (off) state.

        Protocol
        --------
        Host -> Pico: 'r'
        Pico -> Host: 'r'
        """
        reply = self.write_plus_read_until("r")
        if reply != "r":
            raise ValueError(f"Unexpected reply from Pico.\n reply:{reply}")

    def write_digital(self, pin: int, value: int, resistor: str = None):
        """
        Set a GPIO pin as digital output and write a value.

        Parameters
        ----------
        pin : int
            GPIO pin index; range enforced by `PicoHardware.validate_GPIO_pin`.
        value : int
            0 for low, 1 for high.
        resistor : {'n','u','d'} or None
            'n' = no pull, 'u' = pull-up, 'd' = pull-down.
            Use None to let validator apply defaults.

        Raises
        ------
        ValueError
            If value is not 0/1 or Pico reply is unexpected.
        """
        # validation
        PicoHardware.validate_GPIO_pin(pin)
        resistor = PicoHardware.validate_digital_resistor(resistor)
        if value not in (0, 1):
            raise ValueError(f"Digital value can only be [0, 1]. \ngiven: {value}")

        # action
        reply = self.write_plus_read_until(f"d{pin:02}o{resistor}{value}")
        if reply != "d":
            raise ValueError(f"Unexpected reply from Pico.\n reply:{reply}")

        # update pin status
        if value == 1:
            self.pins[pin] = PinStatus.DIGITAL_ON
        else:
            self.pins[pin] = PinStatus.DIGITAL_OFF

    def write_led(self, value: int):
        """
        Toggle the Pico's built-in LED (host-side convenience wrapper).

        Parameters
        ----------
        value : int
            0 for off; 1 for on.
        """
        self.write_digital(PicoHardware.pin_LED, value, 'n')

    def read_digital(self, pin: int, resistor: str = None) -> int:
        """
        Read a GPIO pin as digital input.

        Parameters
        ----------
        pin : int
            GPIO pin index.
        resistor : {'n','u','d'} or None
            Pull configuration when reading input.

        Returns
        -------
        int
            0 or 1 corresponding to logic level.
        """
        # validation
        PicoHardware.validate_GPIO_pin(pin)
        resistor = PicoHardware.validate_digital_resistor(resistor)

        # action
        reply = self.write_plus_read_until(f"d{pin:02}i{resistor}")
        if reply[0] != "d":
            raise ValueError(f"Unexpected reply from Pico.\n reply:{reply}")

        # update pin status
        self.pins[pin] = PinStatus.DIGITAL_INPUT

        return int(reply[1])

    def read_analog(self, pin: int) -> int:
        """
        Read an ADC channel and return the raw 16-bit value.

        Parameters
        ----------
        pin : int
            ADC input index; validated by `PicoHardware.validate_adc_pin`.

        Returns
        -------
        int
            Raw ADC code (0..65535).

        """
        # validation
        PicoHardware.validate_adc_pin(pin)

        # action
        reply = self.write_plus_read_until(f"a{pin:02}")
        if reply[0] != "a":
            raise ValueError(f"Unexpected reply from Pico.\n reply:{reply}")

        # update pin status
        self.pins[pin] = PinStatus.ANALOG

        return int(reply[1:])

    def read_internal_temperature(self) -> Quantity:
        """
        Read the Pico's internal temperature sensor (°C).

        Notes
        -----
        The internal temperature reading depends on the reference voltage and
        is often used for diagnostics rather than precise ambient temperature.

        Returns
        -------
        Quantity
            Temperature in degrees Celsius (rounded to 3 decimals).
        """
        analog = self.read_analog(PicoHardware.pin_internal_temp)
        return round(analog_to_temperature(analog), 3)

    def write_pwm(self, pin: int, duty: int, frequency: int):
        """
        Configure PWM on a GPIO pin (continuous).

        Parameters
        ----------
        pin : int
            GPIO pin index.
        duty : int
            Duty cycle [0..65535]. 0 disables PWM.
        frequency : int
            PWM base frequency [7..125_000_000] (hardware limits apply).
        """
        # validation
        PicoHardware.validate_GPIO_pin(pin)
        PicoHardware.validate_pwm_duty(duty)
        PicoHardware.validate_pwm_frequency(frequency)

        # action
        reply = self.write_plus_read_until(f"p{pin:02}{duty:05}{frequency:09}")
        if reply != "p":
            self._unexpected_reply_from_pico(reply)

        # update pin status
        self.pins[pin] = PinStatus.PWM

    def write_pwm_pulse(self, pin: int, duty: int, frequency: int, time_: Quantity):
        """
        Configure PWM on a GPIO pin for a fixed duration (one-shot pulse).

        Parameters
        ----------
        pin : int
            GPIO pin index.
        duty : int
            Duty cycle [0..65535].
        frequency : int
            PWM base frequency [7..125_000_000].
        time_ : Quantity
            Pulse duration; validated/sanitized by `PicoHardware.validate_pwm_time`.
        """
        # validation
        PicoHardware.validate_GPIO_pin(pin)
        PicoHardware.validate_pwm_duty(duty)
        PicoHardware.validate_pwm_frequency(frequency)
        time_ = PicoHardware.validate_pwm_time(time_)

        # action
        reply = self.write_plus_read_until(f"q{pin:02}{duty:05}{frequency:09}{time_}")
        if reply != "q":
            self._unexpected_reply_from_pico(reply)

    def write_serial(self,
                     message: str,
                     uart_id: int,
                     tx_pin: int,
                     rx_pin: int,
                     baudrate: int = 9600,
                     bits: int = 8,
                     parity: int = None,
                     stop: int = 1
                     ):
        """
        Send a message over UART (TX only) using Pico GPIO pins.

        Parameters
        ----------
        message : str
            Content to transmit (raw).
        uart_id : int
            UART controller index [0, 1].
        tx_pin : int
            TX GPIO pin.
        rx_pin : int
            RX GPIO pin (reserved/configured even if not used here).
        baudrate : int
            Common baud values: 9600, 19200, 57600, 115200.
        bits : int
            Word length [7, 8, 9].
        parity : int or None
            0=even, 1=odd, 2=none (if None provided, defaults to 2 for protocol).
        stop : int
            Stop bits [1, 2].
        """
        # validation
        PicoHardware.validate_uart_pin(uart_id, tx_pin, rx_pin)
        PicoHardware.validate_uart_parameters(baudrate, bits, parity, stop)
        if parity is None:
            parity = 2

        # action
        message_ = f"t{uart_id}{tx_pin:02}{rx_pin:02}{baudrate:06}{bits}{parity}{stop}w{message}"
        reply = self.write_plus_read_until(message_)
        if reply != "t":
            self._unexpected_reply_from_pico(reply)

        # update pin status
        self.pins[tx_pin] = PinStatus.SERIAL
        self.pins[rx_pin] = PinStatus.SERIAL

    def read_serial(self,
                    amount: int,
                    uart_id: int,
                    tx_pin: int,
                    rx_pin: int,
                    baudrate: int = 9600,
                    bits: int = 8,
                    parity: int = None,
                    stop: int = 1
                    ) -> str:
        """
        Receive a fixed number of bytes over UART.

        Parameters
        ----------
        amount : int
            Number of bytes to read.
        uart_id, tx_pin, rx_pin, baudrate, bits, parity, stop
            UART configuration; validated by helpers.

        Returns
        -------
        str
            Raw data payload.
        """
        # validation
        PicoHardware.validate_uart_pin(uart_id, tx_pin, rx_pin)
        PicoHardware.validate_uart_parameters(baudrate, bits, parity, stop)
        if parity is None:
            parity = 2

        # action
        message_ = f"t{uart_id}{tx_pin:02}{rx_pin:02}{baudrate:06}{bits}{parity}{stop}s{amount}"
        reply = self.write_plus_read_until(message_)
        if reply[0] != "t":
            self._unexpected_reply_from_pico(reply)

        # update pin status
        self.pins[tx_pin] = PinStatus.SERIAL
        self.pins[rx_pin] = PinStatus.SERIAL

        return reply[1:]

    def read_until_serial(self,
                          uart_id: int,
                          tx_pin: int,
                          rx_pin: int,
                          baudrate: int = 9600,
                          bits: int = 8,
                          parity: int = None,
                          stop: int = 1
                          ) -> str:
        """
        Receive UART data until the Pico detects a terminator (device-side).

        Parameters
        ----------
        UART configuration fields as above.

        Returns
        -------
        str
            Raw data payload.
        """
        # validation
        PicoHardware.validate_uart_pin(uart_id, tx_pin, rx_pin)
        PicoHardware.validate_uart_parameters(baudrate, bits, parity, stop)
        if parity is None:
            parity = 2

        # action
        message_ = f"t{uart_id}{tx_pin:02}{rx_pin:02}{baudrate:06}{bits}{parity}{stop}r"
        reply = self.write_plus_read_until(message_)
        if reply[0] != "t":
            self._unexpected_reply_from_pico(reply)

        # update pin status
        self.pins[tx_pin] = PinStatus.SERIAL
        self.pins[rx_pin] = PinStatus.SERIAL

        return reply[1:]

    def write_plus_read_until_serial(self,
                                     message: str,
                                     uart_id: int,
                                     tx_pin: int,
                                     rx_pin: int,
                                     baudrate: int = 9600,
                                     bits: int = 8,
                                     parity: int = None,
                                     stop: int = 1
                                     ) -> str:
        """
        Write a UART message, then read back until a terminator (device-side).

        Parameters
        ----------
        message : str
            Payload to send prior to read-until.
        UART configuration fields as above.

        Returns
        -------
        str
            Reply payload.
        """
        # validation
        PicoHardware.validate_uart_pin(uart_id, tx_pin, rx_pin)
        PicoHardware.validate_uart_parameters(baudrate, bits, parity, stop)
        if parity is None:
            parity = 2

        # action
        message_ = f"t{uart_id}{tx_pin:02}{rx_pin:02}{baudrate:06}{bits}{parity}{stop}b{message}"
        reply = self.write_plus_read_until(message_)
        if reply[0] != "t":
            self._unexpected_reply_from_pico(reply)

        # update pin status
        self.pins[tx_pin] = PinStatus.SERIAL
        self.pins[rx_pin] = PinStatus.SERIAL

        return reply[1:]

    def write_spi(self,
                  message: str,
                  spi_id: int,
                  sck_pin: int,
                  mosi_pin: int,
                  miso_pin: int,
                  cs_pin: int,
                  baudrate: int = 115_200,
                  bits: int = 8,
                  polarity: int = 0,
                  phase: int = 0
                  ):
        """
        Transmit data over SPI (TX only).

        Parameters
        ----------
        message : str
            Data to write.
        SPI configuration fields:
            spi_id in {0,1}, pins (sck/mosi/miso/cs), baudrate, bits, polarity, phase.
        """
        # validation
        PicoHardware.validate_spi_pin(spi_id, sck_pin, mosi_pin, miso_pin, cs_pin)
        PicoHardware.validate_spi_parameters(baudrate, bits, polarity, phase)

        # action
        message_ = f"s{spi_id}{sck_pin:02}{mosi_pin:02}{miso_pin:02}{baudrate:06}{bits}{polarity}{phase}" \
                   f"{cs_pin:02}w{message}"
        reply = self.write_plus_read_until(message_)
        if reply != "s":
            self._unexpected_reply_from_pico(reply)

        # update pin status
        self.pins[sck_pin] = PinStatus.SPI
        self.pins[mosi_pin] = PinStatus.SPI
        self.pins[miso_pin] = PinStatus.SPI
        self.pins[cs_pin] = PinStatus.SPI

    def read_spi(self,
                 amount: int,
                 spi_id: int,
                 sck_pin: int,
                 mosi_pin: int,
                 miso_pin: int,
                 cs_pin: int,
                 baudrate: int = 115_200,
                 bits: int = 8,
                 polarity: int = 0,
                 phase: int = 0
                 ) -> str:
        """
        Read a fixed number of bytes over SPI.

        Parameters
        ----------
        amount : int
            Number of bytes to read.
        SPI configuration fields as above.

        Returns
        -------
        str
            Raw data payload.
        """
        # validation
        PicoHardware.validate_spi_pin(spi_id, sck_pin, mosi_pin, miso_pin, cs_pin)
        PicoHardware.validate_spi_parameters(baudrate, bits, polarity, phase)

        # action
        message_ = f"s{spi_id}{sck_pin:02}{mosi_pin:02}{miso_pin:02}{baudrate:06}{bits}{polarity}{phase}" \
                   f"{cs_pin:02}r{amount}"
        reply = self.write_plus_read_until(message_)
        if reply[0] != "s":
            self._unexpected_reply_from_pico(reply)

        # update pin status
        self.pins[sck_pin] = PinStatus.SPI
        self.pins[mosi_pin] = PinStatus.SPI
        self.pins[miso_pin] = PinStatus.SPI
        self.pins[cs_pin] = PinStatus.SPI

        return reply[1:]

    def write_plus_read_spi(self,
                            message: str,
                            amount: int,
                            spi_id: int,
                            sck_pin: int,
                            mosi_pin: int,
                            miso_pin: int,
                            cs_pin: int,
                            baudrate: int = 115_200,
                            bits: int = 8,
                            polarity: int = 0,
                            phase: int = 0
                            ) -> str:
        """
        Write data over SPI and then read a fixed number of response bytes.

        Parameters
        ----------
        message : str
            TX payload.
        amount : int
            RX length to read after write.
        SPI configuration fields as above.

        Returns
        -------
        str
            Reply payload.
        """
        # validation
        PicoHardware.validate_spi_pin(spi_id, sck_pin, mosi_pin, miso_pin, cs_pin)
        PicoHardware.validate_spi_parameters(baudrate, bits, polarity, phase)

        # action
        message_ = f"s{spi_id}{sck_pin:02}{mosi_pin:02}{miso_pin:02}{baudrate:06}{bits}{polarity}{phase}" \
                   f"{cs_pin:02}b{amount:03}{message}"
        reply = self.write_plus_read_until(message_)
        if reply[0] != "s":
            self._unexpected_reply_from_pico(reply)

        # update pin status
        self.pins[sck_pin] = PinStatus.SPI
        self.pins[mosi_pin] = PinStatus.SPI
        self.pins[miso_pin] = PinStatus.SPI
        self.pins[cs_pin] = PinStatus.SPI

        return reply[1:]

    def write_i2c(self,
                  message: str,
                  address: int,
                  i2c_id: int,
                  scl_pin: int,
                  sda_pin: int,
                  frequency: int = 115_200,
                  ):
        """
        Transmit data to an I2C device (write only).

        Parameters
        ----------
        message : str
            Data to write (raw).
        address : int
            7-bit I2C device address.
        i2c_id : int
            I2C controller index [0, 1].
        scl_pin, sda_pin : int
            GPIO pins for SCL/SDA.
        frequency : int
            I2C clock frequency (e.g., 400_000 for 400 kHz).
        """
        # validation
        PicoHardware.validate_i2c_pins(i2c_id, scl_pin, sda_pin)
        PicoHardware.validate_i2c_parameters(frequency, address)

        # action
        reply = self.write_plus_read_until(f"i{i2c_id}{scl_pin:02}{sda_pin:02}{frequency:06}w{message}")
        if reply[0] != "i":
            self._unexpected_reply_from_pico(reply)

        # update pin status
        self.pins[scl_pin] = PinStatus.I2C
        self.pins[sda_pin] = PinStatus.I2C

    def read_i2c(self,
                 amount: int,
                 address: int,
                 i2c_id: int,
                 scl_pin: int,
                 sda_pin: int,
                 frequency: int = 115_200,
                 ) -> str:
        """
        Read a fixed number of bytes from an I2C device.

        Parameters
        ----------
        amount : int
            Number of bytes to read.
        address : int
            7-bit I2C address.
        i2c_id, scl_pin, sda_pin, frequency
            I2C configuration; validated by helpers.

        Returns
        -------
        str
            Reply payload.
        """
        # validation
        PicoHardware.validate_i2c_pins(i2c_id, scl_pin, sda_pin)
        PicoHardware.validate_i2c_parameters(frequency, address)

        # action
        reply = self.write_plus_read_until(f"i{i2c_id}{scl_pin:02}{sda_pin:02}{frequency:06}r{amount}")
        if reply[0] != "i":
            self._unexpected_reply_from_pico(reply)

        # update pin status
        self.pins[scl_pin] = PinStatus.I2C
        self.pins[sda_pin] = PinStatus.I2C

        return reply[1:]

    def read_scan_i2c(self,
                      i2c_id: int,
                      scl_pin: int,
                      sda_pin: int,
                      frequency: int = 115_200,
                      ) -> str:
        """
        Scan I2C bus and return detected device addresses (range 0x08..0x77).

        Parameters
        ----------
        i2c_id, scl_pin, sda_pin, frequency
            I2C configuration; validated by helpers.

        Returns
        -------
        str
            Reply payload (format depends on Pico firmware: typically CSV or raw bytes).
        """
        # validation
        PicoHardware.validate_i2c_pins(i2c_id, scl_pin, sda_pin)

        # action
        reply = self.write_plus_read_until(f"i{i2c_id}{scl_pin:02}{sda_pin:02}{frequency:06}s")
        if reply[0] != "i":
            self._unexpected_reply_from_pico(reply)

        # update pin status
        self.pins[scl_pin] = PinStatus.I2C
        self.pins[sda_pin] = PinStatus.I2C

        return reply[1:]

    def write_plus_read_i2c(self,
                            message: str,
                            amount: int,
                            address: int,
                            i2c_id: int,
                            scl_pin: int,
                            sda_pin: int,
                            frequency: int = 115_200,
                            ):
        """
        Write data to an I2C device and then read a fixed-length response.

        Parameters
        ----------
        message : str
            TX payload.
        amount : int
            Number of RX bytes to read after write.
        address, i2c_id, scl_pin, sda_pin, frequency
            I2C configuration; validated by helpers.

        Returns
        -------
        str
            Reply payload.
        """
        # validation
        PicoHardware.validate_i2c_pins(i2c_id, scl_pin, sda_pin)
        PicoHardware.validate_i2c_parameters(frequency, address)

        # action
        message_ = f"i{i2c_id}{scl_pin:02}{sda_pin:02}{frequency:06}b{amount:03}{message}"
        reply = self.write_plus_read_until(message_)
        if reply[0] != "i":
            self._unexpected_reply_from_pico(reply)

        # update pin status
        self.pins[scl_pin] = PinStatus.I2C
        self.pins[sda_pin] = PinStatus.I2C

        return reply[1:]

    def _unexpected_reply_from_pico(self, reply: str):
        """
        Standardized error path for protocol mismatches.

        Behavior
        --------
        - Sleep briefly to allow any trailing data to arrive.
        - Drain the RX buffer and append it to the error message for debugging.
        - Raise ValueError with the full context.

        Parameters
        ----------
        reply : str
            The unexpected token already read from Pico.
        """
        time.sleep(1)
        reply += self.read_all_buffer()  # get everything from buffer
        raise ValueError(f"Unexpected reply from Pico.\n reply:{reply}")