# send reference_data
"""
TCP server that sends labeled numeric data frames to a PC client and receives control inputs.

Overview
--------
- Listens on a TCP socket (default: 192.168.0.11:5005) and accepts a single client connection.
- (Optionally) sends a JSON list of data labels to the client so it knows the schema.
- Sends data frames as CSV strings (comma-separated numbers) to the client.
- Receives control values from the client as CSV strings and parses them into floats.
- Includes a demo main loop that wires this communication to a motor controller.

Protocol (as used here)
-----------------------
- Labels (optional):
    * Server encodes the labels as JSON and sends:
        1) ASCII length prefix (number of bytes for the JSON payload)
        2) JSON bytes (e.g., ["t","ch1","ch2",...])
    * Server sets `buffer_size` ~ num_fields * 10 (heuristic for CSV payload length).
- Data frames:
    * Server sends a CSV string every loop (e.g., "0.25,30.0").
- Control frames (from client):
    * Server receives a CSV string (e.g., "0.2,10.0") and parses into floats.

Notes
-----
- This is a minimal single-client, blocking design.
- No reconnection or multi-client management is implemented.
- For robust framing and large messages, consider length-prefixed frames on both directions
  and "read exactly N bytes" loops.
"""

import socket
import json
import time

mess_format = 'utf-8'


class intialize_comm:
    """
    Simple TCP server wrapper that accepts a single client and provides helpers
    to send labeled data frames and receive control frames.

    Usage
    -----
        srv = intialize_comm(*data_labels)
        # optionally send labels:
        srv.send_labels(data_labels)
        while True:
            srv.send(data)        # send CSV data frame
            ctrl = srv.recieve()  # receive CSV control values
        srv.disconnect()

    Attributes
    ----------
    TCP_IP : str
        Server bind IP (default: '192.168.0.11').
    TCP_PORT : int
        Server bind port (default: 5005).
    s : socket.socket
        Listening socket.
    conn : socket.socket
        Accepted client connection.
    buffer_size : int
        Expected receive size for a control message (bytes). Set after sending labels
        or defaults to 1024.
    stop_comm : bytes
        Placeholder "stop" token (encoded). Not currently used by the demo loop.
    """

    def __init__(self, *data_labels):
        """
        Bind a TCP socket, listen, and accept a single PC client.

        Parameters
        ----------
        *data_labels : tuple
            Optional labels to send to the client later via `send_labels(...)`.
            (You may also call `send_labels` explicitly.)
        """
        print('Waiting for connection from PC...')
        self.TCP_IP = '192.168.0.11'
        self.TCP_PORT = 5005

        # Create, bind, listen, accept (single connection).
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.bind((self.TCP_IP, self.TCP_PORT))
        self.s.listen(1)
        conn, addr = self.s.accept()
        self.conn = conn
        print('Connection achived with: ', addr)

        # If no labels are passed (empty tuple), use a default buffer_size.
        # (If you will call send_labels later, it will overwrite buffer_size.)
        if data_labels is ():
            self.buffer_size = 1024

        # Encoded "stop" token (not used in this script, retained for protocol symmetry).
        stop_comm = 'stop_comm'
        self.stop_comm = stop_comm.encode(mess_format)

    def send_labels(self, data_labels):
        """
        Send data labels (schema) to the client as a JSON payload with a length prefix.

        Parameters
        ----------
        data_labels : Sequence[str]
            The ordered list of labels describing your data (e.g., ["t","ch1","ch2"]).

        Protocol
        --------
        - Send ASCII length prefix of the JSON-encoded labels.
        - Send the JSON bytes.
        - Set `buffer_size` based on number of fields (heuristic: fields * 10).
        """
        # sending reference_data labels
        self.num_data_points = len(data_labels)
        json_bytes = json.dumps(data_labels).encode(mess_format)
        self.buffer_size = len(json_bytes)

        # Send length prefix, then JSON bytes
        self.conn.sendall(str(self.buffer_size).encode(mess_format))
        self.conn.sendall(json_bytes)

        # Heuristic for incoming CSV control messages: ~10 bytes per field
        self.buffer_size = self.num_data_points * 10  # 4+ each number*sig.fig

    def send(self, data):
        """
        Send a CSV data frame to the client.

        Parameters
        ----------
        data : Iterable
            Numeric values to send, joined by commas (e.g., telemetry frame).
        """
        data_out = ','.join(str(i) for i in data)
        self.conn.sendall(data_out.encode(mess_format))
        return

    def recieve(self):
        """
        Receive a control CSV from the client and parse into floats.

        Returns
        -------
        list[float]
            The parsed control values.

        Notes
        -----
        - Assumes one full CSV frame arrives in a single recv() call and fits in buffer_size.
        - For larger frames or fragmented TCP packets, read in a loop until a delimiter or length is satisfied.
        """
        data_in = self.conn.recv(self.buffer_size)
        data_in = data_in.decode(mess_format)
        data_in = [float(s) for s in data_in.split(',')]
        # print(data_in)
        return data_in

    def disconnect(self):
        """
        Close the listening socket (and implicitly the client connection).
        """
        self.s.close()


if __name__ == '__main__':
    # ---------------------------------------------------------------------
    # Demo: control a small vehicle via keyboard on the PC (client) side.
    # This server runs on the Raspberry Pi. The PC client script is expected
    # to be 'comm_PC_side.py' (paired with this server).
    #
    # The example below depends on custom support code in:
    #   /home/pi/Documents/Projects/Self_drive_car/Support_codes/
    # and will not run without those modules present.
    # ---------------------------------------------------------------------

    import sys

    # Add custom support files path (specific to the author's environment).
    sys.path.insert(1, '/home/pi/Documents/Projects/Self_drive_car/Support_codes/')
    import motor_control_v1 as motor_control
    import Main_run_code_support_v1 as Main_run_code_support

    data_out = [0, 0]  # Example outbound data frame (e.g., telemetry placeholder)
    motors = motor_control.Motor_Control()
    connection = intialize_comm()

    try:
        while True:
            # Send a data frame (telemetry/keep-alive). Adjust for your use case.
            connection.send(data_out)

            # Receive control inputs (e.g., [speed, angle]) from the client
            data_in = connection.recieve()
            print(data_in)

            # Apply control to motors (speed, steering angle)
            motors.motor_update(data_in[0], data_in[1])

    except ValueError:
        # Graceful shutdown if parsing fails / values invalid
        Main_run_code_support.close_out(motors)
        print('Program done!')

    except Exception:
        # Catch-all for unexpected errors; ensure motors are stopped
        Main_run_code_support.close_out(motors)
        print('Unexpected error occured')