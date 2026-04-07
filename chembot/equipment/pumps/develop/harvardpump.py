from __future__ import print_function
import serial
import argparse
import logging


class PumpError(Exception):
    """
    Domain-specific exception for pump-related errors.

    Notes
    -----
    - This is raised on timeouts, out-of-range parameters, unknown replies, etc.
    - The message is meant to be human-readable for logs and quick diagnosis.
    """
    ...


def remove_crud(string: str) -> str:
    """
    Normalize numeric strings returned by the pump.

    Behavior
    --------
    - Removes trailing zeros after a decimal point,
    - Strips trailing decimal points,
    - Strips leading/trailing spaces and leading zeros.

    Parameters
    ----------
    string : str
        Input numeric string from device replies.

    Returns
    -------
    str
        A cleaned numeric string suitable for parsing/echo checks.

    Examples
    --------
    "003.5000 " -> "3.5"
    "30.000"    -> "30"
    "  0.120 "  -> "0.12"
    """
    if "." in string:
        string = string.rstrip('0')

    string = string.lstrip('0 ')
    string = string.rstrip(' .')

    return string


class Chain(serial.Serial):
    """
    Serial chain abstraction for Harvard pumps (daisy-chained pumps on one port).

    Purpose
    -------
    - Subclasses `serial.Serial` and initializes the serial line with the
      typical settings required by Harvard pumps (2 stop bits, no parity).
    - Flushes I/O buffers at initialization (helps avoid stale bytes).
    - Logs creation for run traceability.

    Notes
    -----
    - Each pump on the chain is addressed with a two-digit address, prefixed to
      each command. The chain simply provides the shared serial link.
    """

    def __init__(self, port: str):
        # Initialize parent Serial with device-appropriate settings
        serial.Serial.__init__(self, port=port, stopbits=serial.STOPBITS_TWO, parity=serial.PARITY_NONE, timeout=2)
        self.flushOutput()
        self.flushInput()
        logging.info('Chain created on %s', port)


class Pump:
    """
    Minimal Harvard Pump 11 driver attached to a shared `Chain`.

    Initialization
    --------------
    - Sends 'VER' and expects a 17-byte response, where the last 3 chars are
      `XXY` (XX = address, Y = status ':', '>' or '<'). This validates that:
        * the pump is reachable and
        * the configured address matches.

    Parameters
    ----------
    chain : Chain
        An already-opened serial Chain instance.
    address : int, default=0
        The two-digit address assigned on the pump (e.g., 0..99).
    name : str, default='Pump 11'
        Label used in logs.

    Attributes
    ----------
    serialcon : Chain
        Underlying serial connection (shared among pumps).
    address : str
        Two-digit pump address string.
    diameter : float | None
        Cached diameter set on the device (mm).
    flowrate : str | None
        Cached flow rate set on the device (μL/min) as a string.
    targetvolume : float | None
        Cached target volume set on the device (μL).
    """

    def __init__(self, chain, address: int = 0, name: str = 'Pump 11'):
        self.name = name
        self.serialcon = chain
        self.address = '{0:02.0f}'.format(address)
        self.diameter = None
        self.flowrate = None
        self.targetvolume = None

        # ---- Connectivity check ----
        # Query model/firmware and validate that we reached the correct address.
        # Expected: reply ending with "...XXY" (XX=address, Y=status char)
        try:
            self.write('VER')
            resp = self.read(17)

            if int(resp[-3:-1]) != int(self.address):
                raise PumpError('No response from pump at address %s' %
                                self.address)
        except PumpError:
            self.serialcon.close()
            raise

        logging.info('%s: created at address %s on %s', self.name,
                     self.address, self.serialcon.port)

    def __repr__(self):
        """
        Dump object attributes (developer-friendly inspection).
        """
        string = ''
        for attr in self.__dict__:
            string += '%s: %s\n' % (attr, self.__dict__[attr])
        return string

    # -------------------------
    # Low-level send/receive
    # -------------------------

    def write(self, command: str):
        """
        Send a command to the pump with address prefix and CR terminator.

        Parameters
        ----------
        command : str
            Pump command without address or CR.
        """
        self.serialcon.write((self.address + command + '\r').encode())

    def read(self, bytes: int = 5) -> bytes:
        """
        Read a fixed number of bytes from the pump.

        Parameters
        ----------
        bytes : int, default=5
            Number of bytes to read.

        Returns
        -------
        bytes
            Raw bytes read from the serial connection.

        Raises
        ------
        PumpError
            If no bytes were received within the configured timeout.
        """
        response = self.serialcon.read(bytes)

        if len(response) == 0:
            raise PumpError('%s: no response to command' % self.name)
        else:
            return response

    # -------------------------
    # Device operations
    # -------------------------

    def setdiameter(self, diameter: float):
        """
        Set syringe diameter (millimetres) on the Pump 11.

        Constraints
        -----------
        - Valid range: 0.1 .. 35.0 mm
        - Device considers at most **2 decimal places** (extra precision is truncated).

        Behavior
        --------
        - Sends `MMD<diameter>` and validates by reading back with `DIA`.
        - Logs a warning when truncation occurs; logs error if the echo value differs.

        Raises
        ------
        PumpError
            If out of range or unexpected response.
        """
        if diameter > 35 or diameter < 0.1:
            raise PumpError('%s: diameter %s mm is out of range' %
                            (self.name, diameter))

        # Normalize formatting: device only considers 2 decimal places.
        diameter = str(diameter)

        # Truncate extra dp based on position of decimal point
        if len(diameter) > 5:
            if diameter[2] == '.':  # e.g. 30.2222222
                diameter = diameter[0:5]
            elif diameter[1] == '.':  # e.g. 3.222222
                diameter = diameter[0:4]

            diameter = remove_crud(diameter)
            logging.warning('%s: diameter truncated to %s mm', self.name,
                            diameter)
        else:
            diameter = remove_crud(diameter)

        # Send command
        self.write('MMD' + diameter)
        resp = self.read(5)

        # Pump replies with address and status (:, < or >)
        if (resp[-1] == ':' or resp[-1] == '<' or resp[-1] == '>'):
            # Verify setpoint with DIA
            self.write('DIA')
            resp = self.read(15)
            returned_diameter = remove_crud(resp[3:9])

            if returned_diameter != diameter:
                logging.error('%s: set diameter (%s mm) does not match diameter'
                              ' returned by pump (%s mm)', self.name, diameter,
                              returned_diameter)
            elif returned_diameter == diameter:
                self.diameter = float(returned_diameter)
                logging.info('%s: diameter set to %s mm', self.name,
                             self.diameter)
        else:
            raise PumpError('%s: unknown response to setdiameter' % self.name)

    def setflowrate(self, flowrate: float):
        """
        Set flow rate (microlitres per minute).

        Constraints
        -----------
        - The Pump 11 expects a maximum field width of 5 chars, e.g. "XXXX."
          or "X.XXX". Extra precision is truncated.

        Behavior
        --------
        - Sends `ULM<flowrate>`, then checks with `RAT` and compares the echoed rate.
        - If the pump returns 'OOR', raises `PumpError`.

        Raises
        ------
        PumpError
            On out-of-range or unknown response.
        """
        flowrate = str(flowrate)

        if len(flowrate) > 5:
            flowrate = flowrate[0:5]
            flowrate = remove_crud(flowrate)
            logging.warning('%s: flow rate truncated to %s uL/min', self.name,
                            flowrate)
        else:
            flowrate = remove_crud(flowrate)

        self.write('ULM' + flowrate)
        resp = self.read(5)

        if (resp[-1] == ':' or resp[-1] == '<' or resp[-1] == '>'):
            # Verify with RAT
            self.write('RAT')
            resp = self.read(150)
            returned_flowrate = remove_crud(resp[2:8])

            if returned_flowrate != flowrate:
                logging.error('%s: set flowrate (%s uL/min) does not match'
                              'flowrate returned by pump (%s uL/min)',
                              self.name, flowrate, returned_flowrate)
            elif returned_flowrate == flowrate:
                self.flowrate = returned_flowrate
                logging.info('%s: flow rate set to %s uL/min', self.name,
                             self.flowrate)
        elif 'OOR' in resp:
            raise PumpError('%s: flow rate (%s uL/min) is out of range' %
                            (self.name, flowrate))
        else:
            raise PumpError('%s: unknown response' % self.name)

    def infuse(self):
        """
        Start infusing.

        Behavior
        --------
        - Issues 'RUN' and expects terminal status '>' (infusing).
        - If it sees '<', reverses with 'REV'.
        - Any other status → error.
        """
        self.write('RUN')
        resp = self.read(5)
        while resp[-1] != '>':
            if resp[-1] == '<':  # wrong direction
                self.write('REV')
            else:
                raise PumpError('%s: unknown response to to infuse' % self.name)
            resp = self.serialcon.read(5)

        logging.info('%s: infusing', self.name)

    def withdraw(self):
        """
        Start withdrawing.

        Behavior
        --------
        - 'REV' then confirm terminal status '<'.
        - If ':' → not running → 'RUN'
        - If '>' → wrong direction → 'REV'
        - Anything else → error.
        """
        self.write('REV')
        resp = self.read(5)

        while resp[-1] != '<':
            if resp[-1] == ':':  # pump not running
                self.write('RUN')
            elif resp[-1] == '>':  # wrong direction
                self.write('REV')
            else:
                raise PumpError('%s: unknown response to withdraw' % self.name)
                break
            resp = self.read(5)

        logging.info('%s: withdrawing', self.name)

    def stop(self):
        """
        Stop pump.

        Behavior
        --------
        - Sends 'STP' and expects terminal status ':'.
        """
        self.write('STP')
        resp = self.read(5)

        if resp[-1] != ':':
            raise PumpError('%s: unexpected response to stop' % self.name)
        else:
            logging.info('%s: stopped', self.name)

    def settargetvolume(self, targetvolume: float):
        """
        Set the target volume to infuse/withdraw (microlitres).

        Behavior
        --------
        - Sends 'MLT<targetvolume>' and expects ':' / '>' / '<' terminal status.

        Raises
        ------
        PumpError
            If the target volume was not accepted.
        """
        self.write('MLT' + str(targetvolume))
        resp = self.read(5)

        # Response should be CRLFXX:, CRLFXX>, CRLFXX< (XX=address).
        # Pump11 returns leading zeros (e.g., 03), PHD2000 may omit them.
        if resp[-1] == ':' or resp[-1] == '>' or resp[-1] == '<':
            self.targetvolume = float(targetvolume)
            logging.info('%s: target volume set to %s uL', self.name,
                         self.targetvolume)
        else:
            raise PumpError('%s: target volume not set' % self.name)

    def waituntiltarget(self):
        """
        Block until the pump has reached the target volume (simple polling).

        Behavior
        --------
        - Sends 'VOL' twice and compares the replies.
        - If the replies are identical, assumes motion has stopped at the target.
        - If ':' is present on first read and it's the first loop, raises (not running).
        """
        logging.info('%s: waiting until target reached', self.name)
        i = 0  # loop counter (first loop detection)

        while True:
            # Read once
            self.serialcon.write(self.address + 'VOL\r')
            resp1 = self.read(15)

            if ':' in resp1 and i == 0:
                raise PumpError('%s: not infusing/withdrawing - infuse or '
                                'withdraw first', self.name)
            elif ':' in resp1 and i != 0:
                # pump has already come to a halt
                logging.info('%s: target volume reached, stopped', self.name)
                break

            # Read again
            self.serialcon.write(self.address + 'VOL\r')
            resp2 = self.read(15)

            # If the two reads match, assume we’ve stopped moving
            if resp1 == resp2:
                logging.info('%s: target volume reached, stopped', self.name)
                break

            i = i + 1


class PHD2000(Pump):
    """
    Harvard PHD2000 driver.

    Differences vs Pump 11
    ----------------------
    - Stop behavior: expects '*' terminal status after 'STP'.
    - Target volume ('MLT') expects input in **mL** (not μL). The helper
      converts user-supplied μL to mL for the device, and stores μL locally.
    """

    def stop(self):
        """
        Stop pump (PHD2000 variant).

        Behavior
        --------
        - Sends 'STP' and expects terminal status '*'.
        """
        self.write('STP')
        resp = self.read(5)

        if resp[-1] == '*':
            logging.info('%s: stopped', self.name)
        else:
            raise PumpError('%s: unexpected response to stop', self.name)

    def settargetvolume(self, targetvolume: float):
        """
        Set the target volume (microlitres), PHD2000 variant.

        Behavior
        --------
        - Converts μL → mL (string, trimmed to 5 chars if necessary).
        - Sends 'MLT<mL>' and expects ':' / '>' / '<' terminal status.
        - On success, stores the original μL value in `self.targetvolume`.
        """
        # PHD2000 expects target volume in mL not uL like the Pump11, so convert to mL
        targetvolume = str(float(targetvolume) / 1000.0)

        if len(targetvolume) > 5:
            targetvolume = targetvolume[0:5]
            logging.warning('%s: target volume truncated to %s mL', self.name, targetvolume)

        self.write('MLT' + targetvolume)
        resp = self.read(5)

        if resp[-1] == ':' or resp[-1] == '>' or resp[-1] == '<':
            # Store μL locally
            self.targetvolume = float(targetvolume) * 1000.0
            logging.info('%s: target volume set to %s uL', self.name,
                         self.targetvolume)


def run_local():
    """
    Local quick-start helper.

    Behavior
    --------
    - Opens COM5 and instantiates a `Pump` at address 1.
    - Adjust the COM port and address for your setup.
    """
    chain = Chain("COM5")
    pump = Pump(chain, address=1)


if __name__ == '__main__':
    run_local()