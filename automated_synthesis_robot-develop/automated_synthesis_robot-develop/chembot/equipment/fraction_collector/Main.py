# This code is the main program for operating a fraction collector via a network connection.
# It expects a PC-side controller to be running first, then connects to it over TCP and
# waits for instructions such as: setting the rack layout, homing (zeroing), moving to a
# specific tube, and ending the session.
#
# IMPORTANT
# ---------
# - Start the PC-side program first (it must be listening on the given IP/port).
# - This script acts as a TCP client; the PC acts as the TCP server/controller.

import socket
import Position_Class
import Stepper_Motor_Class

mess_format = 'utf-8'


class SocketFractionCollector:
    """
    TCP client wrapper for a fraction collector.

    Responsibilities
    ----------------
    - Establish a TCP connection to a PC (server) at a known IP/port.
    - Receive simple CSV-like commands and dispatch them to a fraction collector object.
    - Send an "OK" acknowledgement back after each valid command.
    - Close the socket on "End" or on shutdown.

    Protocol (as implemented)
    -------------------------
    - Messages are received as bytes, decoded to UTF-8, and split by comma.
      The first field is a message type; the second field is message data.

      Examples:
        "Layout,XYZ"      → sets FC.layout
        "Zero,_"          → calls FC.zero()
        "Tube,A1"         → calls FC.move("A1")
        "End,_"           → disconnects and requests termination

    Notes
    -----
    - The code expects `Position_Class` to provide a fraction collector class with
      attributes/methods: `.layout`, `.zero()`, and `.move(code)`.
    - `Stepper_Motor_Class.cleanup()` is called on shutdown to ensure GPIO drivers
      are reset (depends on that module).
    """

    def __init__(self):
        """
        Initialize the TCP client and connect to the PC host.

        Attributes set
        --------------
        self.TCP_IP : str
            The IP address of the PC server to connect to.
        self.TCP_PORT : int
            The TCP port on which the server is listening.
        self.s : socket.socket
            Connected TCP socket.
        self.buffer_size : int
            Receive buffer size (bytes).
        """
        print('Trying to connect to PC...')
        self.TCP_IP = '192.168.0.2'  # PC host IP address (adjust to your network)
        self.TCP_PORT = 5005
        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.connect((self.TCP_IP, self.TCP_PORT))
        print('Connection achieved.')
        self.buffer_size = 1024

    def receive_instructions(self, FC):
        """
        Receive a single instruction from the PC, act on it, and send an acknowledgement.

        Parameters
        ----------
        FC : object
            Fraction collector controller instance (from Position_Class) that must support:
            - attribute: FC.layout
            - methods:   FC.zero(), FC.move(position_code)

        Returns
        -------
        str | None
            Returns "break" when an "End" message is received so the main loop can exit;
            otherwise returns None.

        Behavior
        --------
        - Waits on the socket for a single message (blocking recv).
        - Decodes and splits by comma into (msg_type, msg_data).
        - Executes one of:
            "Layout" → set FC.layout
            "Zero"   → FC.zero()
            "Tube"   → FC.move(msg_data)
            "End"    → close socket and request exit
          Otherwise, sends an error back and terminates the program.
        - Sends "OK" after successful handling of the message (except on "End").
        """
        # Listen for messages
        msg = self.s.recv(self.buffer_size)

        # Decode message
        msg = msg.decode(mess_format)
        msg = msg.split(",")
        msg_type = msg[0]
        msg_data = msg[1]

        # Dispatch by message type
        if msg_type == "Layout":
            FC.layout = msg_data
            print("Test tube rack layout is: ", FC.layout)

        elif msg_type == "Zero":
            FC.zero()
            print("All axis have been zeroed.")
            print("Tube Position: A1")

        elif msg_type == "Tube":
            print("Tube Position: " + msg_data)
            FC.move(msg_data)

        elif msg_type == "End":
            # Request graceful shutdown
            self.disconnect()
            return "break"

        else:
            # Unexpected command type → send error and exit
            msg = "Wrong message type received from main computer"
            self.s.send(bytes(msg, mess_format))
            exit(msg)

        # Send confirmation for handled commands
        self.s.send(bytes("OK", mess_format))

    def disconnect(self):
        """
        Close the TCP connection to the PC.
        """
        self.s.close()


if __name__ == '__main__':
    # Start client connection to the PC
    connection = SocketFractionCollector()

    # Instantiate/control the fraction collector
    # NOTE: The code as given calls Position_Class() directly. In many implementations,
    # Position_Class would expose a FractionCollector class, for example:
    #   FC = Position_Class.FractionCollector(plot=True, motor=False)
    # Make sure Position_Class matches this usage in your project.
    FC = Position_Class()  # Position_Class.FractionCollector(plot=True, motor=False)

    out = "Error check"
    try:
        while True:
            # Continuously listen for and process incoming instructions
            return_exit = connection.receive_instructions(FC)
            if return_exit == "break":
                out = "OK"
                print("Program done with no errors.")
                break

    finally:
        # Always clean up motors/GPIO (implementation depends on Stepper_Motor_Class)
        if out != "OK":
            print("Unexpected error occurred.")
        Stepper_Motor_Class.cleanup()