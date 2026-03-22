# recieve reference_data
"""
TCP client for real-time data exchange with a Raspberry Pi (or other host).

Overview
--------
- Connects to a TCP server (default: 192.168.0.11:5005).
- Receives a JSON list of data labels (once) to understand the data schema.
- Receives subsequent numeric CSV payloads and converts them into a NumPy array.
- Sends control/command values (e.g., 'motor_data') back to the server as CSV.
- Includes an interactive CLI loop (under __main__) that:
    * Reads keyboard input to adjust motor speed/angle.
    * Sends the adjusted control values back to the server.
    * Continuously receives data frames.

Protocol (as used here)
-----------------------
- Data labels:
    1) Client receives a small message indicating the byte length of the next JSON payload.
    2) Client then reads exactly that many bytes and JSON-decodes the data labels.
- Data frames (runtime):
    - Client receives a CSV string representing floats (comma-separated).
    - Client decodes bytes -> str -> NumPy array of floats.
- Control frames (optional):
    - Client sends CSV containing two floats (speed, angle), encoded as UTF-8.

Notes
-----
- This client assumes the server:
    * Sends an ASCII length header (e.g., "42") prior to the JSON label list.
    * Sends data rows as ASCII CSV strings.
- Buffer sizes are approximations; the server must ensure that data rows fit into `buffer_size`.
- The interactive loop depends on the `keyboard` module for non-blocking hotkeys.
"""

import socket
import json
import numpy as np
import time

mess_format = 'utf-8'


class intialize_comm:
    """
    Thin TCP client wrapper around a persistent socket connection.

    Responsibilities
    ----------------
    - Open a TCP connection to a given (IP, port).
    - Receive a labeled schema (data labels) so consumers know the order/meaning of numeric frames.
    - Receive a data frame as CSV -> NumPy float array.
    - Send control values back to the server as CSV.

    Usage
    -----
        conn = intialize_comm()
        conn.receive_data_labels()
        while True:
            data = conn.receive_data()
            conn.send_data([speed, angle])
        conn.disconnect()

    Caution
    -------
    - No reconnection logic is implemented.
    - Buffer sizes are static and assume small messages that fit within a single recv().
      For larger frames, you should implement a proper "read exactly n bytes" loop or
      a framing protocol.
    """

    def __init__(self):
        """
        Initialize and connect the TCP socket to the hardcoded server and port.

        Attributes set
        --------------
        self.TCP_IP : str
            Target server IP (default: '192.168.0.11').
        self.TCP_PORT : int
            Target server port (default: 5005).
        self.s : socket.socket
            Connected TCP socket.
        self.buffer_size : int
            Default size for reads (bytes). Will be updated after labels are received.
        """
        print('Trying to connect to PI...')
        self.TCP_IP = '192.168.0.11'
        self.TCP_PORT = 5005
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.connect((self.TCP_IP, self.TCP_PORT))
        self.buffer_size = 1024
        print('Connected to PI.')

    def receive_data_labels(self):
        """
        Receive and parse the initial data labels from the server.

        Protocol
        --------
        1) Read a small length prefix (ASCII digits) that specifies how many bytes to read next.
        2) Read exactly that many bytes and decode as JSON to get a list of labels.
        3) Set `self.num_data_points` and adjust `self.buffer_size` accordingly.

        Side effects
        ------------
        self.data_labels : list[str]
            The list of data field names received from the server.
        self.num_data_points : int
            Number of fields expected per data frame.
        self.buffer_size : int
            Updated to `num_data_points * 10` (heuristic for CSV row size).
        """
        # Receiving reference_data labels
        self.buffer_size = 64
        mess_length = self.s.recv(self.buffer_size)  # receive ASCII length header
        data_labels = self.s.recv(int(mess_length.decode(mess_format)))  # receive JSON payload of that length
        self.data_labels = json.loads(data_labels.decode(mess_format))
        self.num_data_points = len(self.data_labels)

        # Heuristic buffer: enough bytes for CSV (including commas and decimals)
        self.buffer_size = self.num_data_points * 10
        print(self.data_labels)

    def receive_data(self):
        """
        Receive one CSV data frame and convert it to a NumPy float array.

        Returns
        -------
        np.ndarray
            1-D array of floats parsed from the incoming CSV.

        Notes
        -----
        - Assumes one whole CSV row arrives in a single recv() call. If the server sends
          larger frames or frames that may be split across TCP packets, you should
          implement a proper framing/accumulation mechanism.
        """
        data_in = self.s.recv(self.buffer_size)
        data_in = data_in.decode(mess_format)
        data_in = np.fromstring(data_in, dtype=float, sep=',')
        # print(data_in)
        return data_in

    def send_data(self, data_out=None):
        """
        Send control data back to the server as a CSV string.

        Parameters
        ----------
        data_out : list[float] or tuple[float, ...] or None
            The control values (e.g., motor speed, angle). Defaults to [0, 0].

        Behavior
        --------
        - Joins values with commas, encodes as UTF-8, and sends via the socket.
        """
        if data_out is None:
            data_out = [0, 0]

        data_out = ','.join(str(i) for i in data_out)
        self.s.sendall(data_out.encode(mess_format))
        return

    def disconnect(self):
        """
        Close the TCP socket.
        """
        self.s.close()


if __name__ == '__main__':     # Program starts from here
    import keyboard

    # Establish connection and optional interactive control loop
    connection = intialize_comm()
    motor_data = [0.2, 0.2]  # [speed, angle] defaults

    try:
        while True:
            # Receive and ignore data payload here (you could store or plot it)
            connection.receive_data()

            # Exit on spacebar
            if keyboard.is_pressed(' '):
                print('Data collection ended.')
                break

            # -----------------------
            # Motor speed adjustments
            # -----------------------
            if keyboard.is_pressed('up'):
                if motor_data[0] < 1:
                    motor_data[0] = motor_data[0] + 0.1
                if motor_data[0] > 1:
                    motor_data[0] = 1
            elif keyboard.is_pressed('down'):
                if motor_data[0] > -1:
                    motor_data[0] = motor_data[0] - 0.1
                if motor_data[0] < -1:
                    motor_data[0] = -1
            else:
                # natural decay toward 0 when no key is pressed
                if motor_data[0] >= 0.1:
                    motor_data[0] = motor_data[0] - 0.05
                elif motor_data[0] <= -0.1:
                    motor_data[0] = motor_data[0] + 0.05
                else:
                    motor_data[0] = 0

            # -----------------------
            # Motor angle adjustments
            # -----------------------
            if keyboard.is_pressed('right'):
                if motor_data[1] < 90:
                    motor_data[1] = motor_data[1] + 10
                if motor_data[1] > 90:
                    motor_data[1] = 90
            elif keyboard.is_pressed('left'):
                if motor_data[1] > -90:
                    motor_data[1] = motor_data[1] - 10
                if motor_data[1] < -90:
                    motor_data[1] = -90
            else:
                # natural decay toward 0 degrees when no key is pressed
                if motor_data[1] >= 10:
                    motor_data[1] = motor_data[1] - 5
                elif motor_data[1] <= -10:
                    motor_data[1] = motor_data[1] + 5
                else:
                    motor_data[1] = 0

            # Round values for cleaner output and stable CSV size
            motor_data = [round(i, 2) for i in motor_data]

            # Send updated control values to the server
            connection.send_data(data_out=motor_data)
            # time.sleep(0.1)  # optional pacing

    finally:
        connection.disconnect()
        print('program done!')