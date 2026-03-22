from serial import Serial, PARITY_EVEN, STOPBITS_ONE
from time import time

# -----------------------------------------------------------------------------
# Simple serial round-trip test:
# - Opens a COM port at 115200 baud, 8-E-1 (even parity, 1 stop bit).
# - Writes the single character 'r' and reads one line (up to newline) in return.
# - Repeats for 100 iterations and prints each reply.
# - Prints the effective loop rate (iterations per second) at the end.
#
# Notes:
# - `read_until()` blocks until a newline is received or `timeout` elapses,
#   returning whatever the device has sent up to that point.
# - Ensure the target device echoes or responds per line (\n-terminated) to 'r'.
# - Adjust `port` to your system's COM/tty device.
# -----------------------------------------------------------------------------

port = "COM7"

# Configure the serial port. Timeout of 20 ms keeps the loop snappy while preventing
# indefinite blocking if the device fails to respond with a newline.
serial_comm = Serial(
    port=port,
    baudrate=115200,
    parity=PARITY_EVEN,
    stopbits=STOPBITS_ONE,
    timeout=0.02,  # seconds
)

start = time()

# Outer loop count is 1 here; keep for quick expansion if you want multiple passes.
for _ in range(1):
    # Send 100 requests ("r") and read a line each time.
    for i in range(100):
        # Send the request byte(s). Device-specific: here we ask with "r".
        serial_comm.write("r".encode())

        # Read a line (until '\n' or timeout). Returns bytes; print as-is or decode later.
        message = serial_comm.read_until()

        # Display iteration index and raw response (includes trailing newline if provided).
        print(f"{i}, {message}")

# Compute and print the effective loop rate: how many requests per second were achieved.
elapsed = time() - start
print(100 / elapsed)

print("done")

# Potential exception reference:
# serial.serialutil.SerialException