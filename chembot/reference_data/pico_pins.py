from unitpy import U, Quantity


class PicoHardware:
    """
    Central hardware definition and validation utilities for the Raspberry Pi Pico.

    Purpose
    -------
    - Define valid GPIO, ADC, UART, SPI, and I2C pin mappings.
    - Provide validation helpers for hardware-related parameters.
    - Act as a single source of truth for Pico-specific constraints.
    - Ensure high-level Chembot components never issue invalid low-level commands.

    Notes
    -----
    - All pin numbering follows **GPIO numbering** (except ADC identifiers).
    - Validation methods raise ValueError on invalid configurations.
    """

    # ------------------------------------------------------------------
    # General board properties
    # ------------------------------------------------------------------

    # 16-bit ADC resolution (0–65535)
    ADC_resolution = 65535

    # Valid GPIO pins on the Pico
    pins_GPIO = list(range(0, 22)) + [25, 26, 27, 28]

    # Onboard LED GPIO pin
    pin_LED = 25

    # ADC channel indices (ADC0–ADC4)
    pin_adc = [0, 1, 2, 4]

    # GPIO pins corresponding to ADC channels
    pin_adc_GPIO = [26, 27, 28]

    # Special-purpose ADC channels
    pin_internal_temp = 4  # ADC4 (on-chip temperature sensor)
    pin_vsys = 3           # ADC3 (system voltage)

    # ------------------------------------------------------------------
    # Peripheral pin maps
    # ------------------------------------------------------------------

    # I2C pin mappings (i2c_id → [sda, scl] pairs)
    pin_i2c = {
        0: [
            [0, 1],
            [4, 5],
            [8, 9],
            [12, 13],
            [16, 17],
            [20, 21],
        ],
        1: [
            [2, 3],
            [6, 7],
            [10, 11],
            [14, 15],
            [18, 19],
            [26, 27],
        ],
    }

    # SPI pin mappings (spi_id → [miso, mosi, clk])
    pin_spi = {
        0: [
            [0, 3, 2],
            [4, 7, 6],
            [16, 19, 18],
        ],
        1: [
            [8, 11, 10],
            [12, 15, 14],
        ],
    }

    # UART pin mappings (uart_id → [tx, rx])
    pin_uart = {
        0: [
            [0, 1],
            [12, 13],
            [16, 17],
        ],
        1: [
            [4, 5],
            [8, 9],
        ],
    }

    # Pico supply voltage (used for ADC conversions)
    v_sys = 3.3 * U("volt")

    # ------------------------------------------------------------------
    # GPIO validation
    # ------------------------------------------------------------------

    @classmethod
    def validate_GPIO_pin(cls, pin: int):
        """Raise error if pin is not a valid GPIO."""
        if not cls.is_GPIO_pin(pin):
            raise ValueError("Invalid GPIO Pin for pi pico")

    @classmethod
    def is_GPIO_pin(cls, pin: int) -> bool:
        """Return True if pin is a valid GPIO."""
        return pin in cls.pins_GPIO

    # ------------------------------------------------------------------
    # ADC validation and mapping
    # ------------------------------------------------------------------

    @classmethod
    def validate_adc_pin(cls, pin: int):
        """Raise error if pin is not a valid ADC pin."""
        if not cls.is_adc_pin(pin):
            raise ValueError("Invalid ADC Pin for pi pico.")

    @classmethod
    def is_adc_pin(cls, pin: int) -> bool:
        """Return True if pin is a valid ADC identifier or ADC GPIO."""
        return pin in cls.pin_adc or pin in cls.pin_adc_GPIO

    @classmethod
    def adc_pin_to_GPIO_pin(cls, pin: int):
        """
        Convert ADC index (0–4) to corresponding GPIO pin.
        """
        if pin in cls.pin_adc:
            index = cls.pin_adc.index(pin)
            return cls.pin_adc_GPIO[index]

        raise ValueError("Invalid ADC Pin for pi pico.")

    @classmethod
    def GPIO_pin_to_adc_pin(cls, pin: int):
        """
        Convert ADC GPIO pin to ADC index.
        """
        if pin in cls.pin_adc_GPIO:
            index = cls.pin_adc.index(pin)
            return cls.pin_adc[index]

        raise ValueError("Invalid ADC Pin for pi pico.")

    # ------------------------------------------------------------------
    # Digital I/O helpers
    # ------------------------------------------------------------------

    @staticmethod
    def validate_digital_resistor(resistor: str) -> str:
        """
        Validate digital pull resistor option.

        Valid values:
        - 'u' (pull-up)
        - 'd' (pull-down)
        - 'n' (none)
        """
        if resistor is None:
            return "n"
        if resistor in ("u", "d", "n"):
            return resistor

        raise ValueError("Invalid resistor option for pico digital.")

    # ------------------------------------------------------------------
    # PWM validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_pwm_duty(duty: int):
        """Validate PWM duty cycle (0–65535)."""
        if not (0 <= duty <= 65_535):
            raise ValueError(
                f"pwm duty must be between [0, 65_535].\n given:{duty}"
            )

    @staticmethod
    def validate_pwm_frequency(frequency: int):
        """Validate PWM frequency."""
        if not (7 <= frequency <= 125_000_000):
            raise ValueError(
                f"pwm frequency must be between [0, 125_000_000].\n given:{frequency}"
            )

    @staticmethod
    def validate_pwm_time(time_: Quantity) -> str:
        """
        Convert a duration into Pico-compatible PWM time encoding.

        Returns
        -------
        str
            Encoded string with prefix:
            - 'u' for microseconds
            - 'm' for milliseconds
            - 's' for seconds
        """
        if time_.dimensionality != U("second").dimensionality:
            raise ValueError("Units must be time.")

        if time_.to("s").value > 999:
            raise ValueError("Too large of time for PWM to be on.")

        if time_.to("us").value > 1:
            raise ValueError("Too small of time for PWM to be on.")

        time_ = time_.to("us")
        if int(time_.value) < 999:
            return f"u{int(time_.value)}"

        time_ = time_.to("ms")
        if int(time_.value) < 999:
            return f"m{int(time_.value)}"

        time_ = time_.to("s")
        return f"s{int(time_.value)}"

    # ------------------------------------------------------------------
    # UART validation
    # ------------------------------------------------------------------

    @classmethod
    def validate_uart_pin(cls, uart_id: int, tx_pin: int, rx_pin: int):
        """Validate UART ID and TX/RX pins."""
        if uart_id not in cls.pin_uart:
            raise ValueError("Invalid uart_id.")

        if [tx_pin, rx_pin] not in cls.pin_uart[uart_id]:
            raise ValueError("Invalid tx or rx UART pins")

    @staticmethod
    def validate_uart_parameters(baudrate: int, bits: int, parity: int, stop: int):
        """Validate UART communication parameters."""
        if not isinstance(baudrate, int) or not (9600 <= baudrate <= 115200):
            raise ValueError("Invalid uart baudrate.")
        if bits not in (7, 8, 9):
            raise ValueError("Invalid uart bits.")
        if parity not in (0, 1, 2, None):
            raise ValueError("Invalid uart parity.")
        if stop not in (1, 2):
            raise ValueError("Invalid uart stop.")

    # ------------------------------------------------------------------
    # SPI validation
    # ------------------------------------------------------------------

    @classmethod
    def validate_spi_pin(
        cls,
        spi_id: int,
        sck_pin: int,
        mosi_pin: int,
        miso_pin: int,
        cs_pin: int,
    ):
        """Validate SPI pin assignments."""
        if spi_id not in cls.pin_spi:
            raise ValueError("Invalid spi_id.")

        if [miso_pin, mosi_pin, sck_pin] not in cls.pin_spi[spi_id]:
            raise ValueError(
                "Invalid spi miso_pin, mosi_pin or sck_pin pins."
            )

        cls.validate_GPIO_pin(cs_pin)

    @staticmethod
    def validate_spi_parameters(baudrate: int, bits: int, polarity: int, phase: int):
        """Validate SPI protocol parameters."""
        if not isinstance(baudrate, int) or not (9600 <= baudrate <= 115200):
            raise ValueError("Invalid spi baudrate.")
        if bits not in (7, 8, 9):
            raise ValueError("Invalid spi bits.")
        if polarity not in (0, 1):
            raise ValueError("Invalid spi polarity.")
        if phase not in (0, 1):
            raise ValueError("Invalid spi phase.")

    # ------------------------------------------------------------------
    # I2C validation
    # ------------------------------------------------------------------

    @classmethod
    def validate_i2c_pins(cls, i2c_id: int, scl_pin: int, sda_pin: int):
        """Validate I2C pin assignments."""
        if i2c_id not in cls.pin_i2c:
            raise ValueError("Invalid i2c id.")

        if [sda_pin, scl_pin] not in cls.pin_i2c[i2c_id]:
            raise ValueError("Invalid i2c scl or sda pins.")

    @staticmethod
    def validate_i2c_parameters(frequency: int, address: int):
        """Validate I2C bus parameters."""
        if not isinstance(frequency, int) or not (9600 <= frequency <= 115200):
            raise ValueError("Invalid i2c frequency.")
        if not isinstance(address, int) or not (8 <= address <= 119):
            raise ValueError("Invalid i2c address.")