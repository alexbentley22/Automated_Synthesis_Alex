"""
Quick manual test tool for an LED array driven over a serial connection.

Overview
--------
- Discovers available serial ports (once at import) and caches active connections.
- Connects to a specific COM port and sends simple ASCII commands understood by the
  LED-array controller firmware (not shown here).
- Powers on the LED array drivers, then waits for user input like 'r50' (red @ 50%).
- Supports colors: red (r), mint (m), green (g), cyan (c), blue (b), violet (v).
- Type 'e1' (or 'e' with any number) to exit; on exit, sends an 'e' command
  (interpreted here as a global "lights off") and closes out.

Notes
-----
- The exact command grammar is device/firmware-specific:
    * Power enable: "r<pp>1\r" (where <pp> is a two-digit power pin number)
    * Set color:     "l<ch><freq><pct>\r"
                     - <ch>   = 2-digit channel/pin id
                     - <freq> = 4 digits frequency (Hz)
                     - <pct>  = 3 digits duty (0..100)
    * Exit/off:      "e\r"
- You can adapt pin assignments and frequency to match your hardware and firmware.

Linux tip (enumerate USB TTY)
-----------------------------
The block below (commented) shows a shell snippet that lists USB serial devices
with their IDs on Linux, useful for mapping /dev/tty* ports to physical devices.

for linux
for sysdevpath in $(find /sys/bus/usb/devices/usb*/ -name dev); do
    (
        syspath="${sysdevpath%/dev}"
        devname="$(udevadm info -q name -p $syspath)"
        [[ "$devname" == "bus/"* ]] && exit
        eval "$(udevadm info -q property --export -p $syspath)"
        [[ -z "$ID_SERIAL" ]] && exit
        echo "/dev/$devname - $ID_SERIAL"
    )
done
"""

from serial import Serial, PARITY_EVEN, STOPBITS_ONE
from serial.tools.list_ports import comports

# Snapshot of ports the OS currently reports as available.
# If devices are plugged/unplugged later, refresh by calling comports() again.
available_ports = [port.device for port in comports()]
# Cache of ports we have actively opened in this process.
active_ports = {}


def connect_serial(
    comm_port,
    baudrate=115200,
    parity=PARITY_EVEN,
    stopbits=STOPBITS_ONE,
    timeout=0.1
):
    """
    Open (or reuse) a serial connection to the device.

    Parameters
    ----------
    comm_port : str
        OS device path/name (e.g., 'COM4' on Windows, '/dev/ttyUSB0' on Linux).
    baudrate : int
        Serial baud rate (default 115200).
    parity : enum
        Parity setting (default PARITY_EVEN).
    stopbits : enum
        Stop bits (default STOPBITS_ONE).
    timeout : float
        Read timeout in seconds (default 0.1 s).

    Returns
    -------
    serial.Serial
        An open serial connection. If this port is already open in `active_ports`,
        the cached handle is returned; otherwise a new connection is created.

    Notes
    -----
    - This function checks against the initial snapshot in `available_ports`.
      If the device was plugged in after import, call `comports()` again externally
      and update `available_ports` or bypass the check for quick tests.
    """
    if comm_port in active_ports.keys():
        # If we already have a handle for this port, return it.
        return active_ports[comm_port]
    else:
        if comm_port in available_ports:
            # Create a new handle
            serial = Serial(
                port=comm_port,
                baudrate=baudrate,
                stopbits=stopbits,
                parity=parity,
                timeout=timeout
            )
            active_ports[comm_port] = serial
            return serial
        # If the port isn't in the available snapshot, no connection is returned.
        # You could raise an exception here or refresh `available_ports` as needed.


if __name__ == '__main__':
    # -------------------------
    # User-configurable section
    # -------------------------
    test_serial = connect_serial('COM4')  # Adjust to match your system
    # Channel/pin mapping for your LED array controller
    red_pin = 0
    mint_pin = 1
    green_pin = 2
    cyan_pin = 3
    blue_pin = 4
    violet_pin = 5

    # "Power enable" (or driver enable) pins, if your controller splits logic.
    power_pin = 15
    power_pin2 = 14

    # Default PWM frequency for LED channels
    freq = 300

    # -------------------------
    # Session header / enable outputs
    # -------------------------
    print("\nType 'e1' then 'enter' to exit.")  # 'e' + anything: exit
    # Enable power for banks (device-specific 'r<pp>1\\r' command)
    mes = "r" + str(power_pin).zfill(2) + "1" + "\r"
    test_serial.write(mes.encode())
    mes = "r" + str(power_pin2).zfill(2) + "1" + "\r"
    test_serial.write(mes.encode())

    # Print initial reply (if any) and prompt for interactive mode
    print(test_serial.read_until())
    print("")
    print("Enter 'r,m,g,c,b,v' followed by a power level [0..100], e.g., 'r25' (red 25%).")

    try:
        while True:
            try:
                # -------------------------
                # Read input and parse command
                # -------------------------
                input_string = input()
                color = input_string[0]       # one of r/m/g/c/b/v/e
                power = int(input_string[1:]) # 0..100

                if 0 <= power <= 100:
                    # Build message for the selected color channel.
                    # Format: 'l<ch><freq><pct>\\r' → set LED channel to % at frequency Hz
                    if color == "r":
                        mes = "l" + str(red_pin).zfill(2) + str(freq).zfill(4) + str(power).zfill(3) + "\r"
                    elif color == "m":
                        mes = "l" + str(mint_pin).zfill(2) + str(freq).zfill(4) + str(power).zfill(3) + "\r"
                    elif color == "g":
                        mes = "l" + str(green_pin).zfill(2) + str(freq).zfill(4) + str(power).zfill(3) + "\r"
                    elif color == "c":
                        mes = "l" + str(cyan_pin).zfill(2) + str(freq).zfill(4) + str(power).zfill(3) + "\r"
                    elif color == "b":
                        mes = "l" + str(blue_pin).zfill(2) + str(freq).zfill(4) + str(power).zfill(3) + "\r"
                    elif color == "v":
                        mes = "l" + str(violet_pin).zfill(2) + str(freq).zfill(4) + str(power).zfill(3) + "\r"
                    elif color == "e":
                        # Exit command shortcut even if a number follows (e.g., 'e1')
                        print("exit")
                        break
                    else:
                        print("Invalid color. Use one of: r m g c b v (or 'e' to exit).")
                        continue

                    # Send command and print the controller's reply line (if any)
                    test_serial.write(mes.encode())
                    print(test_serial.read_until())

                else:
                    print("Power outside valid range (0..100).")

            except Exception:
                # Swallow parsing errors to keep the interactive loop responsive.
                # You may want to print/log the exception in a debug build.
                pass

    finally:
        # On exit, send a global "off" command (device-specific 'e\\r')
        mes = "e" + "\r"
        test_serial.write(mes.encode())
        print(test_serial.read_until())
        print("lights off, program done")