# This code is to test the fraction collector operation from the PC side.
# It runs as a TCP SERVER on the PC and waits for a fraction-collector client (e.g., Raspberry Pi) to connect.
#
# Modes
# -----
# 1) Zero all axes
# 2) Zero all axes + user-entered tube moves
# 3) Zero all axes + automatic sweep through all tubes (entire rack)
# 4) Zero all axes + automatic long-term repeatability test (toggle between two positions)
#
# IMPORTANT
# ---------
# - Start this server FIRST, then start the client on the fraction collector.
# - This server sends commands like "Layout,<id>", "Zero", "Tube,<label>", and expects "OK" replies.
# - The client must implement the same simple CSV-like protocol and reply "OK" per command.

import socket
from string import ascii_uppercase
from time import sleep

mess_format = 'utf-8'


class SeverComm:
    """
    Simple TCP server for controlling a fraction collector client.

    Responsibilities
    ----------------
    - Bind/listen on a TCP port and accept exactly one incoming connection.
    - Send single-line commands: "Layout,<id>", "Zero", "Tube,<label>".
    - Wait for an "OK" acknowledgement after each command.
    - Provide a "disconnect" command ("End,End") to close the session.

    Protocol
    --------
    - The server sends a CSV-like message: "<Type>,<Payload>" over TCP (UTF-8).
    - The client must reply with "OK" for valid commands, or an error string otherwise.
    - The server finally sends "End,End" to ask the client to close and then closes the socket.

    Notes
    -----
    - Buffer sizes must match client/server expectations for reliable reads.
    - This is a minimal blocking implementation intended for bench testing.
      It does not handle partial frames, timeouts, or multi-client scenarios.
    """

    def __init__(self):
        """
        Start the TCP server and accept one client connection.

        Attributes set
        --------------
        self.TCP_IP : str
            Local IP address this server binds to (derived from hostname).
        self.TCP_PORT : int
            TCP port for the control link (default: 5005).
        self.s : socket.socket
            Listening socket; also used to accept the first client.
        self.conn : socket.socket
            Accepted client connection socket (one client only).
        self.buffer_size : int
            Receive buffer size in bytes.
        """
        print('Starting server')
        self.TCP_IP = socket.gethostbyname(socket.gethostname())
        self.TCP_PORT = 5005
        print('Server IP address: ', self.TCP_IP, '  Server Port: ', self.TCP_PORT)

        # Create, bind, listen, and accept a single connection
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        print('Listening for connections...')
        self.s.bind((self.TCP_IP, self.TCP_PORT))
        self.s.listen(1)
        client_connection, client_address = self.s.accept()
        self.conn = client_connection
        print('Connection achieved with: ', client_address)

        # Max size of messages to be sent/received (simple guard)
        self.buffer_size = 1024

    def send_data(self, data_type, data_out="None"):
        """
        Send a single command to the client and wait for "OK" acknowledgement.

        Parameters
        ----------
        data_type : str
            Message type (e.g., "Layout", "Zero", "Tube").
        data_out : str
            Payload (e.g., "23ml_test_tubes" for layout, "A1" for tube label).
            If not needed, defaults to "None".

        Raises
        ------
        SystemExit
            If the outgoing message exceeds buffer constraints or if the
            reply is not "OK".

        Notes
        -----
        - Command is serialized as "data_type,data_out" (comma-separated).
        - This routine is intentionally simple and blocking. For robustness in
          production, add framing (e.g., newline-delimited or length-prefixed)
          and error handling (timeouts, retries).
        """
        # Sanity check: keep some headroom for safety (headers/terminator if added later)
        if len(data_out) < self.buffer_size - 20:
            if isinstance(data_out, str):
                msg = data_type + ',' + data_out
                self.conn.send(bytes(msg, mess_format))
        else:
            exit("The data that is being sent is too long. Adjust message or buffer (both in client and server).")

        # Wait for confirmation
        confirmation = self.conn.recv(self.buffer_size)
        if "OK" != confirmation.decode(mess_format):
            exit("Error message received:" + confirmation.decode(mess_format))

    def disconnect(self):
        """
        Request the client to close and shut down the server socket.
        """
        self.conn.send(bytes("End" + "," + "End", mess_format))
        self.s.close()


if __name__ == '__main__':     # Program starts from here
    # Parameters
    FC_layout = '23ml_test_tubes'   # rack layout identifier (client must understand this)
    mode = 1                        # choose 1..4 per description above

    # Start the server and wait for client
    FC_connection = SeverComm()

    # Send tray layout and then zero
    FC_connection.send_data("Layout", FC_layout)

    try:
        if mode == 1:
            # Mode 1: Zero all axes
            print('Sending zero all axis command.')
            FC_connection.send_data("Zero")
            print('All axis have been zeroed.')

        elif mode == 2:
            # Mode 2: Zero all axes, then user-driven moves by tube label
            print('Sending zero all axis command.')
            FC_connection.send_data("Zero")
            print('All axis have been zeroed.')

            print("To End the program enter 'end'. ")
            while True:
                vial = input("Please enter tube (format: A1, B2, etc.):")
                if vial == 'end':
                    break
                FC_connection.send_data("Tube", vial)

        elif mode == 3:
            # Mode 3: Zero all axes, then automatically sweep through the rack
            print('Sending zero all axis command.')
            FC_connection.send_data("Zero")
            print('All axis have been zeroed.')

            # Define all tube spots for the given layout
            if FC_layout == '23ml_test_tubes':
                LETTERS = list(ascii_uppercase[:12])                  # rows A..L (12 rows)
                NUMBERS = list(map(str, list(range(1, 16))))         # columns 1..15

                print(LETTERS)
                print(NUMBERS)

            print("To End the program enter 'end'. ")
            for letter in LETTERS:
                for num in NUMBERS:
                    vial = letter + num
                    print("Moving to position: " + vial)
                    FC_connection.send_data("Tube", vial)

        elif mode == 4:
            # Mode 4: Zero all axes, then long-term repeatability test toggling between two tubes
            print('Sending zero all axis command.')
            FC_connection.send_data("Zero")
            print('All axis have been zeroed.')

            cycles = 10
            for i in range(cycles):
                FC_connection.send_data("Tube", "D3")
                print("Moving to position: D3")
                sleep(5)
                FC_connection.send_data("Tube", "J13")
                print("Moving to position: J13")
                print("Cycle " + str(i + 1) + " out of " + str(cycles))
                sleep(5)

        else:
            exit("Not a valid mode.")

    finally:
        # Always attempt to cleanly disconnect
        FC_connection.disconnect()
        print('Program done!')