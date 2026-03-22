"""
Simple serial tester for a 'phase sensor' device connected via a USB‑to‑UART
adapter (e.g., FT232R) to a Raspberry Pi Pico or similar MCU that speaks a
very small ASCII protocol.

Wiring / Setup
--------------
Check the serial port in: Device Manager → Ports (Windows) or `ls /dev/tty*` (Linux/macOS).

Typical wiring (FT232R ↔ Pico):
    FT232R    ↔    Pico
    RX        ↔    TX (GPIO_0, UART0)
    TX        ↔    RX (GPIO_1, UART0)
    GND       ↔    GND
    VCC       ↛    (do NOT connect — 5 V can damage Pico if powered from USB)
    DTR/RTS   ↔    Any GPIO (optional, e.g., GPIO_2)   # RTS: Request To Send (polarity device‑specific)
    CTS       ↛    (unused)                             # CTS: Clear To Send (polarity device‑specific)

Troubleshooting
---------------
If you are not receiving any messages, the most common issue is swapping RX/TX.
Try swapping RX and TX lines (cross them) and verify the baud/parity/stop bits.
"""

from time import sleep

from serial import Serial, STOPBITS_TWO, PARITY_EVEN, PARITY_NONE, STOPBITS_ONE


def phase_sensor():
    """
    Minimal interactive loop for the phase sensor.

    Protocol (as used here)
    -----------------------
    - Send 'r' : device responds with 'r' when it is ready / just reset
    - Send 's' : device responds with 's' when in "run" (streaming/ready) mode

    Flow
    ----
    1) Open COM port with the specified settings.
    2) Try to enter 'run' state by repeatedly sending 'r' and waiting for an 'r' reply.
    3) Print 10 lines read from the device, sending 'r' between reads.
    4) While in 'run', send 's' periodically and check that the device replies 's'.

    Notes
    -----
    - This is a very bare-bones smoke test; it does not parse payloads or handle errors robustly.
    - Adjust 'port', parity, and stopbits to match your device firmware.
    """
    port = "COM6"
    # Serial parameters MUST match the device firmware (57600‑E‑8‑1 here).
    serial_comm = Serial(port=port, baudrate=57600, parity=PARITY_EVEN, stopbits=STOPBITS_ONE, timeout=1)

    state = "standby"

    # Handshake: repeatedly send 'r' until device echoes 'r', then declare "running".
    while state == "standby":
        serial_comm.write("r".encode())
        message = serial_comm.read_until().decode().replace("\n", "")
        if message == "r":
            state = "run"
            print("running")
            break
        print(f"trying to connect {port}")
        sleep(1)

    # Read a few lines as a basic sanity check, sending 'r' between reads.
    for _ in range(10):
        print(serial_comm.read_until())
        serial_comm.write("r".encode())

    # While running, periodically send 's' and expect an 's' reply.
    # (This loop is currently a no‑op with respect to state changes, but asserts connectivity.)
    while state == "run":
        serial_comm.write("s".encode())
        message = serial_comm.read_until().decode().replace("\n", "")
        if message == "s":
            state = "run"

    print("comm done")


def phase_sensor2():
    """
    Alternative probing routine for the phase sensor with a slightly different order:

    Flow
    ----
    1) Open COM port (57600‑E‑8‑1).
    2) Loop sending 'r' and print any replies until a non‑'s' response is observed.
    3) Send 'r' 50 times at 100 ms intervals and print each reply.
    4) Attempt to enter 'standby' by sending 's' and verifying the device echoes 's' (loop until it does).

    Notes
    -----
    - The naming 'state' is mostly nominal here; the key behavior is printing whatever comes back.
    - Use this when the first routine doesn't show useful output; it exercises slightly different code paths.
    """
    port = "COM6"
    serial_comm = Serial(port=port, baudrate=57600, parity=PARITY_EVEN, stopbits=STOPBITS_ONE, timeout=1)

    # Step 1: Probe with 'r' until response differs from 's'
    state = "run"
    message = "s"
    while message == "s":
        serial_comm.write("r".encode())
        message = serial_comm.read_until().decode().replace("\n", "")
        print(message)

    # Step 2: Send 'r' repeatedly, printing what we see (basic link exercise)
    state = "run"
    for _ in range(50):
        serial_comm.write("r".encode())
        message = serial_comm.read_until().decode().replace("\n", "")
        print(message)
        sleep(0.1)

    # Step 3: Attempt to reach/confirm 'standby' by sending 's' and expecting 's' back
    while state == "standby":
        serial_comm.write("s".encode())
        message = serial_comm.read_until().decode().replace("\n", "")
        print(message)
        if message == "s":
            state = "standby"

    print("comm done")


if __name__ == '__main__':
    # Entry point: run the second probing routine by default.
    # Change to `phase_sensor()` if you prefer the first.
    phase_sensor2()