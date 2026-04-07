import time

import serial
import argparse
import logging


class SerialConnection(serial.Serial):
    """
    Thin wrapper over `serial.Serial` to create a connection with
    the typical settings used by Harvard pumps and log creation.

    Settings
    --------
    - baudrate = 9600
    - stopbits = 2
    - timeout  = 2 s

    Behavior
    --------
    - Flushes output and input buffers immediately after opening,
      which helps avoid stale bytes on initial communications.
    - Logs the port used for traceability.
    """
    def __init__(self, port):
        serial.Serial.__init__(self, port=port, baudrate=9600, stopbits=2, timeout=2)
        self.flushOutput()
        self.flushInput()
        logging.info("Created serial connection on %s", port)


class PumpError(Exception):
    """Domain-specific exception for pump errors (timeouts, bad replies, out-of-range settings, etc.)."""
    pass


def remove_string_extras(string: str) -> str:
    """
    Normalize numeric strings coming from device echoes/replies.

    Behavior
    --------
    - If a decimal point is present, strip trailing zeros after it.
    - Strip leading zeros and spaces.
    - Strip trailing spaces and solitary decimal points.

    Examples
    --------
    "003.5000 " -> "3.5"
    "30.000"    -> "30"
    "  0.120 "  -> "0.12"
    """
    if "." in string:
        string = string.rstrip("0")
    string = string.lstrip("0 ")
    string = string.rstrip(" .")
    return string


class Pump2000:
    """
    Harvard PHD2000 (legacy protocol) minimal driver.

    Purpose
    -------
    - Provides helper methods to set diameter, flow rates, volumes, and to start/stop
      infuse/withdraw operations using the PHD2000 command set (e.g., 'DIA', 'RAT', 'TGT', 'RUN').
    - Validates the address and connectivity by querying 'VER' at construction.

    Parameters
    ----------
    serial_connection : SerialConnection
        An open serial connection.
    address : int | str
        Pump address (00-99). Stored and sent as a two-digit string.
    name : str
        Label used in logs.

    Notes
    -----
    - This class uses **address-prefixed** commands and expects **fixed-length reads**.
    - The script compares terminal status characters using HTML-escaped symbols
      ("&gt;" for '>' and "&lt;" for '<') to match the source data as provided.
    """
    # defines Pump2000 class with serial connection, address, and name
    # (used in logging)
    def __init__(self, serial_connection, address, name):
        self.serialcon = serial_connection
        self.address = "{0:02.0f}".format(int(address))
        self.name = name
        # every time Pump2000 is defined, version() tests writing/reading to pump at address
        self.version()

    def write_read(self, command, bytes=20):
        """
        Write a command (with address prefix and CR terminator) and read a fixed number of bytes.

        Parameters
        ----------
        command : str
            Command without address or terminator.
        bytes : int
            Number of bytes to read for the reply.

        Returns
        -------
        str
            Decoded reply string (using default decoder; device sends ASCII).
        """
        # print(str.encode(str(self.address) + command + '\r'))
        self.serialcon.write(str.encode(str(self.address) + command + "\r"))
        response = self.serialcon.read(bytes)
        # print(response)
        # print(response.decode())
        return response.decode()

    def version(self, bytes=60):
        """
        Query firmware/version to validate connectivity and correct address.

        Behavior
        --------
        - Sends 'VER' and inspects the tail of the reply for the address and status.
        - If '*' is seen (target reached), clears target volume and tries again.
        - Validates against 1-digit (0x) or 2-digit (xx) addresses.

        Raises
        ------
        PumpError
            If no response, or address mismatch is detected.
        """
        version_pump = self.write_read("VER", bytes=bytes)
        if (
            len(version_pump) == 0
        ):  # no response... probably not connected correctly or maybe a baud rate error
            logging.error(
                "%s: not connected to pump correctly at address %s",
                self.name,
                self.address,
            )
            self.serialcon.close()
            raise PumpError("Not connected to pump correctly")
        if (
            version_pump[-1] == "*"
        ):  # target volume had been reached, need to clear target volume then try again
            self.clear_target_volume(bytes=60)
            version_pump = self.write_read("VER", bytes=bytes)
        if self.address[0] == "0":  # address is only 1 digit
            if (
                version_pump[-2:-1] == self.address[1]
            ):  # last character on response matches address
                logging.info("%s: created at address %s", self.name, self.address)
            else:
                logging.error(
                    "%s: not connected to pump correctly at address %s",
                    self.name,
                    self.address,
                )
                self.serialcon.close()
                raise PumpError("wrong address")
        else:  # address is not 1 digit (2 digit address)
            if (
                version_pump[-3:-1] == self.address
            ):  # last 2 characters on response match address
                logging.info("%s: created at address %s", self.name, self.address)
            else:
                logging.error(
                    "%s: not connected to pump correctly at address %s",
                    self.name,
                    self.address,
                )
                self.serialcon.close()
                raise PumpError("wrong address")

    def set_dia(self, diameter, bytes=60):
        """
        Set syringe diameter (mm) with range and precision checks.

        Constraints
        -----------
        - 0.1 mm <= diameter <= 45 mm
        - Device accepts up to ~2 decimal places; longer strings are truncated here.

        Validation
        ----------
        - Reads back with 'DIA' and compares the normalized value.

        Raises
        ------
        PumpError
            If out of range or the echo does not match after setting.
        """
        if (
            float(diameter) > 45 or float(diameter) < 0.1
        ):  # checks if diameter is within
            logging.error(
                "%s: diameter (%s mm) is out of range", self.name, str(diameter)
            )
            self.serialcon.close()
            raise PumpError("diameter is out of range")
        if (
            len(remove_string_extras(str(diameter))) > 6
        ):  # checks if diameter needs to be truncated
            new_d = remove_string_extras(str(diameter))[0:6]
            logging.info(
                "%s: set diameter (%s mm) has been truncated to %s mm",
                self.name,
                str(diameter),
                str(new_d),
            )
            diameter = new_d
        diam_resp = self.write_read("DIA", bytes=60)
        diam_response = remove_string_extras(remove_string_extras(diam_resp[3:9]))
        if float(diam_response) == float(
            remove_string_extras(str(diameter))
        ):  # checks if currently set diameter
            # matches the new diameter argument
            logging.info(
                "%s: did not update diameter because set diameter (%s mm) already matches diameter returned by pump ("
                "%s mm)",
                self.name,
                str(diameter),
                diam_response,
            )
        else:  # if new diameter does not match the last set diameter, then diameter is updated
            self.write_read("DIA " + str(diameter), bytes=60)
            diam = self.write_read(
                "DIA", bytes=60
            )  # checks updated diameter matches the newly set diameter
            dia_response = remove_string_extras(remove_string_extras(diam[3:9]))
            if float(dia_response) == float(remove_string_extras(str(diameter))):
                logging.info("%s: diameter set to %s mm", self.name, str(diameter))
            else:
                logging.error(
                    "%s: set diameter (%s mm) does not match diameter returned by pump (%s mm)",
                    self.name,
                    str(diameter),
                    dia_response,
                )
                self.serialcon.close()
                raise PumpError("Diameter not updated correctly")

    def set_infuse_rate(self, infuse_rate, infuse_rate_units):
        """
        Set **infuse** rate (flow out of syringe).

        Units (device grammar)
        ----------------------
        - 'ml/min' -> "RAT <value> MM"
        - 'ul/min' -> "RAT <value> UM"
        - 'ml/hr'  -> "RAT <value> MH"
        - 'ul/hr'  -> "RAT <value> UH"

        Behavior
        --------
        - Truncates overly long numeric strings.
        - Sends the rate, checks for 'Out of range', and verifies via 'RAT' echo.

        Raises
        ------
        PumpError
            If out of range or echoed value mismatches.
        """
        # choices=['ul/hr', 'ul/min', 'ml/hr', 'ml/min']
        if (
            len(remove_string_extras(str(infuse_rate))) > 6
        ):  # checks if flow rate needs to be truncated
            new_ir = remove_string_extras(str(infuse_rate))[0:6]
            logging.info(
                "%s: set infuse rate (%s %s) has been truncated to %s %s",
                self.name,
                str(infuse_rate),
                infuse_rate_units,
                str(new_ir),
                infuse_rate_units,
            )
            infuse_rate = new_ir
        if infuse_rate_units == "ml/min":  # command depends on rate units
            irate = self.write_read("RAT " + str(infuse_rate) + " MM", bytes=60)
        elif infuse_rate_units == "ul/min":
            irate = self.write_read("RAT " + str(infuse_rate) + " UM", bytes=60)
        elif infuse_rate_units == "ml/hr":
            irate = self.write_read("RAT " + str(infuse_rate) + " MH", bytes=60)
        elif infuse_rate_units == "ul/hr":
            irate = self.write_read("RAT " + str(infuse_rate) + " UH", bytes=60)
        irate_resp = self.write_read("RAT", bytes=60)  # reads updated flow rate
        if (
            "Out of range" in irate
        ):  # if flow rate is out of the range for the syringe's diameter
            logging.error(
                "%s: infuse rate (%s %s) is out of range",
                self.name,
                str(infuse_rate),
                infuse_rate_units,
            )
            self.serialcon.close()
            raise PumpError("Infuse rate is out of range")
        if float(remove_string_extras(irate_resp[3:9]).split(" ")[0]) == float(
            infuse_rate
        ):  # checks updated flow rate matches newly set flow rate
            logging.info(
                "%s: infuse rate set to %s %s",
                self.name,
                str(infuse_rate),
                infuse_rate_units,
            )
        else:
            logging.error(
                "%s: set infuse rate (%s %s) does not match flowrate returned by pump (%s)",
                self.name,
                str(infuse_rate),
                infuse_rate_units,
                remove_string_extras(irate_resp[3:9]).split(" ")[0],
            )
            self.serialcon.close()
            raise PumpError("Infuse Rate not updated correctly")

    def set_withdraw_rate(self, withdraw_rate, withdraw_rate_units):
        """
        Set **withdraw** rate (flow into syringe).

        Units (device grammar)
        ----------------------
        - 'ml/min' -> "RFR <value> MM"
        - 'ul/min' -> "RFR <value> UM"
        - 'ml/hr'  -> "RFR <value> MH"
        - 'ul/hr'  -> "RFR <value> UH"

        Raises
        ------
        PumpError
            If out of range or echoed value mismatches.
        """
        # choices=['ul/hr', 'ul/min', 'ml/hr', 'ml/min']
        if (
            len(remove_string_extras(str(withdraw_rate))) > 6
        ):  # checks if flow rate needs to be truncated
            new_wr = remove_string_extras(str(withdraw_rate))[0:6]
            logging.info(
                "%s: set withdraw rate (%s %s) has been truncated to %s %s",
                self.name,
                str(withdraw_rate),
                withdraw_rate_units,
                str(new_wr),
                withdraw_rate_units,
            )
            withdraw_rate = new_wr
        if withdraw_rate_units == "ml/min":  # command depends on rate units
            wrate = self.write_read("RFR " + str(withdraw_rate) + " MM", bytes=60)
        elif withdraw_rate_units == "ul/min":
            wrate = self.write_read("RFR " + str(withdraw_rate) + " UM", bytes=60)
        elif withdraw_rate_units == "ml/hr":
            wrate = self.write_read("RFR " + str(withdraw_rate) + " MH", bytes=60)
        elif withdraw_rate_units == "ul/hr":
            wrate = self.write_read("RFR " + str(withdraw_rate) + " UH", bytes=60)
        wrate_resp = self.write_read("RFR", bytes=60)  # reads updated flow rate
        if (
            "Out of range" in wrate
        ):  # if flow rate is out of the range for the syringe's diameter
            logging.error(
                "%s: withdraw rate (%s %s) is out of range",
                self.name,
                str(withdraw_rate),
                withdraw_rate_units,
            )
            self.serialcon.close()
            raise PumpError("withdraw rate is out of range")
        if float(remove_string_extras(wrate_resp[3:9]).split(" ")[0]) == float(
            withdraw_rate
        ):  # checks updated flow rate matches newly set flow rate
            logging.info(
                "%s: withdraw rate set to %s %s",
                self.name,
                str(withdraw_rate),
                withdraw_rate_units,
            )
        else:
            logging.error(
                "%s: set withdraw rate (%s %s) does not match flowrate returned by pump (%s)",
                self.name,
                str(withdraw_rate),
                withdraw_rate_units,
                remove_string_extras(wrate_resp[3:9]).split(" ")[0],
            )
            self.serialcon.close()
            raise PumpError("Withdraw Rate not updated correctly")

    def set_target_volume(self, target_volume, target_volume_units, bytes=60):
        """
        Set the **target volume** for volumetric mode.

        Behavior
        --------
        - Switches mode to volume ('MOD VOL').
        - Converts μL -> mL when units are 'ul'.
        - Normalizes/truncates the numeric string to device-friendly width.
        - Sends 'TGT <ml>' to set target.

        Notes
        -----
        Device may echo with different formats; this method logs the setpoint.
        """
        self.write_read("MOD " + "VOL", bytes=bytes)  # sets pump to VOL mode
        if (
            target_volume_units == "ul"
        ):  # need to convert target volume (from ul to ml) if target volume unit is ul
            target_volume = float(target_volume) / 1000
        target_volume = "{0:0.6f}".format(float(target_volume))
        if (
            len(remove_string_extras(str(target_volume))) > 6
        ):  # checks if target volume needs to be truncated
            new_tv = remove_string_extras(str(target_volume))[0:6]
            logging.info(
                "%s: set target volume (%s ml) has been truncated to %s ml",
                self.name,
                str(target_volume),
                str(new_tv),
            )
            target_volume = new_tv
        self.write_read("TGT " + str(target_volume), bytes=bytes)  # sets target volume
        logging.info(
            "%s: target volume set to %s %s",
            self.name,
            str(target_volume),
            target_volume_units,
        )

    def set_pump_mode(self, bytes=60):
        """
        Switch to **pump mode** (no target volume; run until stopped).
        """
        # (flow indefinitely instead of flowing until target volume has been reached)
        self.write_read("MOD " + "PMP", bytes=bytes)
        logging.info("%s: set to pump mode", self.name)

    def set_syringe_volume(self, syringe_volume, syringe_volume_units, bytes=60):
        """
        Set the **syringe volume** metadata on the device.

        Notes
        -----
        - Converts μL -> mL when units are 'ul'.
        - Truncates long numeric strings; writes 'SYR <ml>'.
        """
        if (
            syringe_volume_units == "ul"
        ):  # need to convert syringe volume (from ul to ml) if syringe volume unit is ul
            syringe_volume = float(syringe_volume) / 1000
        syringe_volume = "{0:0.6f}".format(float(syringe_volume))
        if (
            len(remove_string_extras(str(syringe_volume))) > 6
        ):  # checks if syringe volume needs to be truncated
            new_tv = remove_string_extras(str(syringe_volume))[0:6]
            logging.info(
                "%s: set syringe volume (%s ml) has been truncated to %s ml",
                self.name,
                str(syringe_volume),
                str(new_tv),
            )
            syringe_volume = new_tv
        self.write_read(
            "SYR " + str(syringe_volume), bytes=bytes
        )  # sets syringe volume
        logging.info(
            "%s: syringe volume set to %s %s",
            self.name,
            str(syringe_volume),
            syringe_volume_units,
        )

    def set_stop(self, bytes=60):
        """
        Stop the pump and validate terminal status.

        Expected terminal status chars
        ------------------------------
        ':' or '*'
        """
        stop_pump = self.write_read("STP", bytes=bytes)
        if (
            stop_pump[-1] == ":" or stop_pump[-1] == "*"
        ):  # checks if pump has correctly stopped
            logging.info("%s: stopped", self.name)
        else:
            logging.error("%s: Incorrect response to stop", self.name)
            self.serialcon.close()
            raise PumpError("Incorrect response to stop")

    def set_irun(self, bytes=60):
        """
        Begin **infuse** (flow out from syringe).

        Steps
        -----
        - 'DIR INF'
        - 'RUN' and expect terminal status '&gt;' (HTML-escaped '>')
        """
        self.write_read("DIR" + " INF", bytes=bytes)  # sets direction
        irun_pump = self.write_read("RUN", bytes=bytes)
        if irun_pump[-1] == "&gt;":  # checks if pump if infusing correctly
            logging.info("%s: Infusing", self.name)
        else:
            logging.error("%s: not infusing correctly", self.name)
            self.serialcon.close()
            raise PumpError("Incorrect response to irun")

    def set_wrun(self, bytes=60):
        """
        Begin **withdraw** (flow into syringe).

        Steps
        -----
        - 'DIR REF'
        - 'RUN' and expect terminal status '&lt;' (HTML-escaped '<')
        """
        self.write_read("DIR" + " REF", bytes=bytes)  # sets direction
        wrun_pump = self.write_read("RUN", bytes=bytes)
        if wrun_pump[-1] == "&lt;":  # checks if pump if withdrawing correctly
            logging.info("%s: Withdrawing", self.name)
        else:
            logging.error("%s: not withdrawing correctly", self.name)
            self.serialcon.close()
            raise PumpError("Incorrect response to wrun")

    def wait_for_target(self, i_or_w, bytes=60):
        """
        Start (infuse/withdraw) and then wait until target is reached (basic polling).

        Behavior
        --------
        - For 'infuse': issue 'DIR INF'+'RUN' (via set_irun), then read 'DEL' to check delivered volume.
        - For 'withdraw': issue 'DIR REF'+'RUN' (via set_wrun), then read 'DEL'.
        - If ':' is seen on first pass → not running; if ':' on later pass → target reached.
        """
        if i_or_w == "infuse":
            self.set_irun(bytes=60)  # set pump to infuse
            i = 0
            while True:
                wait_resp = self.write_read(
                    "DEL", bytes=bytes
                )  # check volume delivered
                if ":" == wait_resp[-1] and i == 0:
                    logging.error("%s: not infusing or withdrawing", self.name)
                    self.serialcon.close()
                    raise PumpError("not infusing or withdrawing")
                elif ":" == wait_resp[-1] and i != 0:
                    logging.info("%s: target volume has been reached", self.name)
                    break
                else:
                    break
                i = i + 1
        elif i_or_w == "withdraw":
            self.set_wrun(bytes=60)  # set pump to withdraw
            i = 0
            while True:
                wait_resp = self.write_read(
                    "DEL", bytes=bytes
                )  # check volume delivered
                if ":" == wait_resp[-1] and i == 0:
                    logging.error("%s: not infusing or withdrawing", self.name)
                    self.serialcon.close()
                    raise PumpError("not infusing or withdrawing")
                elif ":" == wait_resp[-1] and i != 0:
                    logging.info("%s: target volume has been reached", self.name)
                    break
                else:
                    break
                i = i + 1
        else:
            logging.error("%s: need flow direction (infuse or withdraw)", self.name)
            self.serialcon.close()
            raise PumpError("need flow direction (infuse or withdraw)")

    def set_poll(self, i_or_w, bytes=60):
        """
        Poll delivered volume / status and log whether stopped or still running.

        Behavior
        --------
        - Uses 'DEL' and interprets terminal status char:
            ':' or '*' → stopped
            '&gt;'     → infusing
            '&lt;'     → withdrawing
        """
        poll_pump = self.write_read("DEL", bytes=bytes)
        if poll_pump[-1] == ":" or poll_pump[-1] == "*":  # pump has stopped
            logging.info("%s: pump has stopped", self.name)
        elif poll_pump[-1] == "&gt;":  # still infusing
            logging.info(
                "%s: has not reached target volume and is still infusing", self.name
            )
        elif poll_pump[-1] == "&lt;":  # still withdrawing
            logging.info(
                "%s: has not reached target volume and is still withdrawing", self.name
            )
        else:
            logging.error("%s: Incorrect response to polling", self.name)
            self.serialcon.close()
            raise PumpError("Incorrect response to polling")

    def clear_target_volume(self, bytes=60):
        """
        Clear delivered and target volumes (reset counters), logging the action.
        """
        self.write_read("CLD", bytes=bytes)
        logging.info("%s: volume has been cleared", self.name)


class PumpUltra:
    """
    Harvard PHD Ultra minimal driver.

    Purpose
    -------
    - Similar structure to `Pump2000`, but uses the Ultra's text commands:
      * 'ver', 'diameter', 'irate', 'wrate', 'tvolume', 'svolume', 'stop', 'irun', 'wrun', etc.
    - Validates address using 'ver' response tail.

    Notes
    -----
    - String slicing offsets differ from Pump2000 ('diameter' echoes are parsed from the
      tail of the line, for example).
    """
    # defines PumpUltra class with serial connection, address, and name (used in logging)
    def __init__(self, chain, address, name):
        self.name = name
        self.serialcon = chain
        self.address = "{0:02.0f}".format(int(address))
        # every time PumpUltra is defined, version() tests writing/reading to pump at address
        self.version()

    def write_read(self, command, bytes=20):
        """
        Write + read helper (address-prefixed ASCII command, fixed-size read).
        """
        # print(str.encode(str(self.address) + command + '\r'))
        self.serialcon.write(str.encode(str(self.address) + command + "\r"))
        response = self.serialcon.read(bytes)
        # print(response)
        # print(response.decode())
        return response.decode()

    def version(self, bytes=60):
        """
        Validate connectivity and correct address via 'ver'.

        Raises
        ------
        PumpError
            If no response or the address substring does not match.
        """
        version_pump = self.write_read("ver", bytes=bytes)
        if (
            len(version_pump) == 0
        ):  # no response... probably not connected correctly or maybe a baud rate error
            logging.error(
                "%s: not connected to pump correctly at address %s",
                self.name,
                self.address,
            )
            self.serialcon.close()
            raise PumpError("Not connected to pump correctly")
        if (
            version_pump[-1] == "*"
        ):  # target volume had been reached, need to clear target volume then try again
            self.clear_target_volume(bytes=60)
            version_pump = self.write_read("ver", bytes=bytes)
        if (
            version_pump[-3:-1] == self.address
        ):  # last 2 characters on response matches address
            logging.info("%s: created at address %s", self.name, self.address)
        else:
            logging.error(
                "%s: not connected to pump correctly at address %s",
                self.name,
                self.address,
            )
            self.serialcon.close()
            raise PumpError("wrong address")

    def set_dia(self, diameter, bytes=60):
        """
        Set syringe diameter (mm) on PHD Ultra.

        Validation
        ----------
        - Checks 0.1 <= diameter <= 45.
        - Truncates overly long numeric strings.
        - Reads/compares echo via 'diameter'.
        """
        if (
            float(diameter) > 45 or float(diameter) < 0.1
        ):  # checks if diameter is within
            logging.error(
                "%s: diameter (%s mm) is out of range", self.name, str(diameter)
            )
            self.serialcon.close()
            raise PumpError("diameter is out of range")
        if (
            len(remove_string_extras(str(diameter))) > 6
        ):  # checks if diameter needs to be truncated
            new_d = remove_string_extras(str(diameter))[0:6]
            logging.info(
                "%s: set diameter (%s mm) has been truncated to %s mm",
                self.name,
                str(diameter),
                str(new_d),
            )
            diameter = new_d
        diam_resp = self.write_read("diameter", bytes=60)
        dia_response = remove_string_extras(remove_string_extras(diam_resp[-15:-8]))
        if float(dia_response) == float(
            remove_string_extras(str(diameter))
        ):  # checks if currently set diameter matches the new diameter argument
            logging.info(
                "%s: did not update diameter because set diameter (%s mm) already matches diameter returned by pump ("
                "%s mm)",
                self.name,
                str(diameter),
                dia_response,
            )
        else:  # if new diameter does not match the last set diameter, then diameter is updated
            self.write_read("diameter " + str(diameter), bytes=60)
            diam = self.write_read(
                "diameter", bytes=60
            )  # checks updated diameter matches the newly set diameter
            dia_response = remove_string_extras(remove_string_extras(diam[-15:-8]))
            if float(dia_response) == float(remove_string_extras(str(diameter))):
                logging.info("%s: diameter set to %s mm", self.name, str(diameter))
            else:
                logging.error(
                    "%s: set diameter (%s mm) does not match diameter returned by pump (%s mm)",
                    self.name,
                    str(diameter),
                    dia_response,
                )
                self.serialcon.close()
                raise PumpError("Diameter not updated correctly")

    def set_infuse_rate(self, infuse_rate, infuse_rate_units):
        """
        Set **infuse** rate on PHD Ultra.

        Command format
        --------------
        'irate <value> <units>'
        """
        if (
            len(remove_string_extras(str(infuse_rate))) > 6
        ):  # checks if flow rate needs to be truncated
            new_ir = remove_string_extras(str(infuse_rate))[0:6]
            logging.info(
                "%s: set infuse rate (%s %s) has been truncated to %s %s",
                self.name,
                str(infuse_rate),
                infuse_rate_units,
                str(new_ir),
                infuse_rate_units,
            )
            self.serialcon.close()
            infuse_rate = new_ir
        irate = self.write_read(
            "irate " + str(infuse_rate) + " " + infuse_rate_units, bytes=60
        )  # set infuse rate
        irate_resp = self.write_read("irate", bytes=60)  # reads updated flow rate
        irate_respr = irate_resp.split(":")[1]
        if (
            "Out of range" in irate
        ):  # if flow rate is out of the range for the syringe's diameter
            logging.error(
                "%s: infuse rate (%s %s) is out of range",
                self.name,
                str(infuse_rate),
                infuse_rate_units,
            )
            self.serialcon.close()
            raise PumpError("Infuse rate is out of range")
        if float(remove_string_extras(irate_respr[0:12]).split(" ")[0]) == float(
            str(infuse_rate)
        ):
            logging.info(
                "%s: infuse rate set to %s %s",
                self.name,
                str(infuse_rate),
                infuse_rate_units,
            )
        else:
            logging.error(
                "%s: set infuse rate (%s %s) does not match flowrate returned by pump (%s)",
                self.name,
                str(infuse_rate),
                infuse_rate_units,
                remove_string_extras(irate_respr[0:12]).split(" ")[0],
            )
            self.serialcon.close()
            raise PumpError("Infuse Rate not updated correctly")

    def set_withdraw_rate(self, withdraw_rate, withdraw_rate_units):
        """
        Set **withdraw** rate on PHD Ultra.

        Command format
        --------------
        'wrate <value> <units>'
        """
        if (
            len(remove_string_extras(str(withdraw_rate))) > 6
        ):  # checks if flow rate needs to be truncated
            new_wr = remove_string_extras(str(withdraw_rate))[0:6]
            logging.info(
                "%s: set withdraw rate (%s %s) has been truncated to %s %s",
                self.name,
                str(withdraw_rate),
                withdraw_rate_units,
                str(new_wr),
                withdraw_rate_units,
            )
            withdraw_rate = new_wr
        wrate = self.write_read(
            "wrate " + str(withdraw_rate) + " " + withdraw_rate_units, bytes=60
        )  # set withdraw rate
        wrate_resp = self.write_read("wrate", bytes=60)  # reads updated flow rate
        wrate_respr = wrate_resp.split(":")[1]
        if (
            "Out of range" in wrate
        ):  # if flow rate is out of the range for the syringe's diameter
            logging.error(
                "%s: withdraw rate (%s %s) is out of range",
                self.name,
                str(withdraw_rate),
                withdraw_rate_units,
            )
            self.serialcon.close()
            raise PumpError("Withdraw rate is out of range")
        if float(remove_string_extras(wrate_respr[0:12]).split(" ")[0]) == float(
            str(withdraw_rate)
        ):  # checks updated flow rate matches newly set flow rate
            logging.info(
                "%s: withdraw rate set to %s %s",
                self.name,
                str(withdraw_rate),
                withdraw_rate_units,
            )
        else:
            logging.error(
                "%s: set withdraw rate (%s %s) does not match flowrate returned by pump (%s)",
                self.name,
                str(withdraw_rate),
                withdraw_rate_units,
                remove_string_extras(wrate_respr[0:12]).split(" ")[0],
            )
            self.serialcon.close()
            raise PumpError("Withdraw Rate not updated correctly")

    def set_target_volume(self, target_volume, target_volume_units, bytes=60):
        """
        Set target volume on PHD Ultra:
          'tvolume <value> <units>'
        """
        self.clear_target_volume(bytes=bytes)
        if (
            len(remove_string_extras(str(target_volume))) > 6
        ):  # checks if target volume needs to be truncated
            new_tv = remove_string_extras(str(target_volume))[0:6]
            logging.info(
                "%s: set target volume (%s %s) has been truncated to %s %s",
                self.name,
                str(target_volume),
                target_volume_units,
                str(new_tv),
                target_volume_units,
            )
            target_volume = new_tv
        self.write_read(
            "tvolume " + str(target_volume) + " " + target_volume_units, bytes=bytes
        )  # sets target volume
        logging.info(
            "%s: target volume set to %s %s",
            self.name,
            str(target_volume),
            target_volume_units,
        )

    def set_syringe_volume(self, syringe_volume, syringe_volume_units, bytes=60):
        """
        Set syringe volume metadata on PHD Ultra:
          'svolume <value> <units>'
        """
        if (
            len(remove_string_extras(str(syringe_volume))) > 6
        ):  # checks if syringe volume needs to be truncated
            new_tv = remove_string_extras(str(syringe_volume))[0:6]
            logging.info(
                "%s: set syringe volume (%s %s) has been truncated to %s %s",
                self.name,
                str(syringe_volume),
                syringe_volume_units,
                str(new_tv),
                syringe_volume_units,
            )
            syringe_volume = new_tv
        self.write_read(
            "svolume " + str(syringe_volume) + " " + syringe_volume_units, bytes=bytes
        )  # sets syringe volume
        logging.info(
            "%s: syringe volume set to %s %s",
            self.name,
            str(syringe_volume),
            syringe_volume_units,
        )

    def set_stop(self, bytes=60):
        """
        Stop (PHD Ultra variant):
          'stop' -> expect ':' terminal status.
        """
        stop_pump = self.write_read("stop", bytes=bytes)
        if stop_pump[-1] == ":":  # checks if pump has correctly stopped
            logging.info("%s: stopped", self.name)
        else:
            logging.error("%s: Incorrect response to stop", self.name)
            self.serialcon.close()
            raise PumpError("Incorrect response to stop")

    def set_irun(self, bytes=60):
        """
        Begin **infuse**:
          'irun' -> expect '&gt;' terminal status (HTML-escaped '>').
        """
        irun_pump = self.write_read("irun", bytes=bytes)
        if irun_pump[-1] == "&gt;":  # checks if pump if infusing correctly
            logging.info("%s: Infusing", self.name)
        else:
            logging.error("%s: not infusing correctly", self.name)
            self.serialcon.close()
            raise PumpError("Incorrect response to irun")

    def set_wrun(self, bytes=60):
        """
        Begin **withdraw**:
          'wrun' -> expect '&lt;' terminal status (HTML-escaped '<').
        """
        wrun_pump = self.write_read("wrun", bytes=bytes)
        if wrun_pump[-1] == "&lt;":  # checks if pump if withdrawing correctly
            logging.info("%s: Withdrawing", self.name)
        else:
            logging.error("%s: not withdrawing correctly", self.name)
            self.serialcon.close()
            raise PumpError("Incorrect response to wrun")

    def wait_for_target(self, i_or_w, bytes=60):
        """
        Run (infuse/withdraw) and wait for target (Ultra variant).

        Behavior
        --------
        - For 'infuse': 'irun', then poll 'ivolume'.
        - For 'withdraw': 'wrun', then poll 'wvolume'.
        - ':' on first pass → not running; '*' later → target reached.
        """
        if i_or_w == "infuse":
            self.write_read("irun", bytes=bytes)  # set pump to infuse
            i = 0
            while True:
                wait_resp = self.write_read(
                    "ivolume", bytes=bytes
                )  # check volume delivered
                if ":" == wait_resp[-1] and i == 0:
                    logging.info("%s: not infusing or withdrawing", self.name)
                    self.serialcon.close()
                    raise PumpError("not infusing or withdrawing")
                elif "*" == wait_resp[-1] and i != 0:
                    logging.info("%s: target volume has been reached", self.name)
                    break
                else:
                    break
                i = i + 1
        elif i_or_w == "withdraw":
            self.write_read("wrun", bytes=bytes)  # set pump to withdraw
            i = 0
            while True:
                wait_resp = self.write_read(
                    "wvolume", bytes=bytes
                )  # check volume delivered
                if ":" == wait_resp[-1] and i == 0:
                    logging.info("%s: not infusing or withdrawing", self.name)
                    self.serialcon.close()
                    raise PumpError("not infusing or withdrawing")
                elif "*" == wait_resp[-1] and i != 0:
                    logging.info("%s: target volume has been reached", self.name)
                    break
                else:
                    break
                i = i + 1
        else:
            logging.error("%s: need flow direction (infuse or withdraw)", self.name)
            self.serialcon.close()
            raise PumpError("need flow direction (infuse or withdraw)")

    def set_poll(self, i_or_w, bytes=60):
        """
        Poll volume/status (Ultra variant).

        Commands
        --------
        - 'ivolume' (infuse) or 'wvolume' (withdraw)
        - Logs status character to indicate stopped / infusing / withdrawing.
        """
        if i_or_w == "infuse":
            poll_pump = self.write_read("ivolume", bytes=bytes)
        elif i_or_w == "withdraw":
            poll_pump = self.write_read("wvolume", bytes=bytes)
        else:
            logging.error("%s: need flow direction (infuse or withdraw)", self.name)
            self.serialcon.close()
            raise PumpError("need flow direction (infuse or withdraw)")
        if poll_pump[-1] == ":":  # pump has stopped
            logging.info("%s: pump has stopped", self.name)
        elif poll_pump[-1] == "&gt;":  # still infusing
            logging.info(
                "%s: has not reached target volume and is still infusing", self.name
            )
        elif poll_pump[-1] == "&lt;":  # still withdrawing
            logging.info(
                "%s: has not reached target volume and is still withdrawing", self.name
            )
        else:
            logging.error("%s: Incorrect response to polling", self.name)
            self.serialcon.close()
            raise PumpError("Incorrect response to polling")

    def clear_target_volume(self, bytes=60):
        """
        Clear target-volume related counters (Ultra variant): 'cvolume', 'ctvolume'.
        """
        self.write_read("cvolume", bytes=bytes)
        self.write_read("ctvolume", bytes=bytes)
        logging.info("%s: volume has been cleared", self.name)


if __name__ == "__main__":
    # ----------------------------------------------------------------------------------
    # Quick local example (imperative): open COM10, address 0, set diameter and infuse
    # ----------------------------------------------------------------------------------
    sc_com4 = SerialConnection('COM10')
    address = 0
    pump_2000_1 = PumpUltra(sc_com4, address, name='PHD2000_1')
    diameter = 9.5
    pump_2000_1.set_dia(diameter)

    # pump_2000_1.set_pump_mode()

    infuse_rate = 10
    infuse_rate_units = 'ul/min'
    pump_2000_1.set_infuse_rate(infuse_rate, infuse_rate_units)

    pump_2000_1.set_irun()

    time.sleep(5)
    pump_2000_1.set_stop()

    # ----------------------------------------------------------------------------------
    # Command-line interface (commented path begins after exit()).
    # Pass flags to set rates/volumes and run the pump from the shell.
    # ----------------------------------------------------------------------------------

    exit()
    parser = argparse.ArgumentParser(
        description="pump_code_pack controls syringe pumps from the command line."
    )
    parser.add_argument("-p", dest="usbport", help="serial port")
    parser.add_argument("-a", dest="address", help="address")
    parser.add_argument("-d", dest="diameter", help="diameter in mm")
    parser.add_argument("-i", dest="infuse_rate", help="infuse rate")
    parser.add_argument(
        "-iu",
        dest="infuse_rate_units",
        choices=["ul/hr", "ul/min", "ml/hr", "ml/min"],
        help="infuse rate units. only choose: ul/hr, ul/min, ml/hr, ml/min ",
    )
    parser.add_argument("-w", dest="withdraw_rate", help="withdraw rate")
    parser.add_argument(
        "-wu",
        dest="withdraw_rate_units",
        choices=["ul/hr", "ul/min", "ml/hr", "ml/min"],
        help="withdraw rate units. only choose: ul/hr, ul/min, ml/hr, ml/min ",
    )
    parser.add_argument("-t", dest="target_volume", help="target volume")
    parser.add_argument(
        "-tu",
        dest="target_volume_units",
        choices=["ul", "ml"],
        help="target volume units. only choose: ul, ml",
    )
    parser.add_argument("-s", dest="syringe_volume", help="syringe volume")
    parser.add_argument(
        "-su",
        dest="syringe_volume_units",
        choices=["ul", "ml"],
        help="syringe volume units. only choose: ul, ml",
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-infuse", action="store_true", help="set pump to infuse")
    group.add_argument("-withdraw", action="store_true", help="set pump to withdraw")
    group.add_argument("-stop", action="store_true", help="stop pump")
    group.add_argument(
        "-infuse_wait",
        action="store_true",
        help="set pump to infuse and wait until target volume has been reached",
    )
    group.add_argument(
        "-withdraw_wait",
        action="store_true",
        help="set pump to infuse and wait until target volume has been reached",
    )
    group.add_argument(
        "-poll", action="store_true", help="check if pump has reached target volume"
    )
    group.add_argument(
        "-poll_infuse",
        action="store_true",
        help="check if pump has reached target volume (after infusing)",
    )
    group.add_argument(
        "-poll_withdraw",
        action="store_true",
        help="check if pump has reached target volume (after withdrawing)",
    )

    pumpgroup = parser.add_mutually_exclusive_group()
    pumpgroup.add_argument("-PHD2000", help="To control PHD2000", action="store_true")
    pumpgroup.add_argument(
        "-PHDULTRA", help="To control PHD Ultra", action="store_true"
    )
    args = parser.parse_args()

    print("Make sure you have set the address the pump address on the physical pump")
    print(
        "also, make sure you have the correct baud rate on the pump: baud rate = 9600"
    )

    chain = SerialConnection(args.usbport)

    address = "{0:02.0f}".format(int(args.address))
    try:
        if args.PHDULTRA:  # if PHDULTRA is an argument from the command line
            pump_ultra = PumpUltra(
                chain, args.address, name="PHDULTRA"
            )  # define PumpUltra class
            if args.stop:  # check if stop is an argument
                stop_pump = pump_ultra.set_stop(bytes=60)
            elif args.poll:  # check if poll is an argument
                poll_pump = pump_ultra.set_poll(i_or_w="infuse", bytes=60)
            elif args.poll_infuse:  # check if poll for infusing is an argument
                poll_pump = pump_ultra.set_poll(i_or_w="infuse", bytes=60)
            elif args.poll_withdraw:  # check if poll for withdrawing is an argument
                poll_pump = pump_ultra.set_poll(i_or_w="withdraw", bytes=60)
            else:
                if args.diameter:  # if diameter is an argument, set diameter at pump
                    dia_pump = pump_ultra.set_dia(args.diameter, bytes=60)
                if (
                    args.syringe_volume
                ):  # if syringe volume is an arugment, set syringe volume
                    sv_pump = pump_ultra.set_syringe_volume(
                        args.syringe_volume, args.syringe_volume_units, bytes=60
                    )
                if (
                    args.target_volume
                ):  # if target volume is an arugment,  set target volume
                    tv_pump = pump_ultra.set_target_volume(
                        args.target_volume, args.target_volume_units, bytes=60
                    )
                if args.infuse_rate:  # if infuse rate is an arugment, set infuse rate
                    if args.infuse_rate_units:  # also needs infuse rate units
                        ir_pump = pump_ultra.set_infuse_rate(
                            args.infuse_rate, args.infuse_rate_units
                        )
                    else:  # if not units send error
                        logging.error(
                            "%s: Incorrect response to polling", pump_ultra.name
                        )
                        chain.close()
                        raise PumpError("Need infuse rate units")
                if (
                    args.withdraw_rate
                ):  # if withdraw rate is an arugment, set infuse rate
                    if args.withdraw_rate_units:  # also needs withdraw rate units
                        wr_pump = pump_ultra.set_withdraw_rate(
                            args.withdraw_rate, args.withdraw_rate_units
                        )
                    else:  # if not units send error
                        logging.error(
                            "%s: Incorrect response to polling", pump_ultra.name
                        )
                        chain.close()
                        raise PumpError("Need withdraw rate units")
                if args.infuse:
                    irun = pump_ultra.set_irun(
                        bytes=60
                    )  # runs the pump in the infuse direction
                if args.withdraw:
                    wrun = pump_ultra.set_wrun(
                        bytes=60
                    )  # runs the pump in the withdraw direction
                if args.infuse_wait:
                    iw = pump_ultra.wait_for_target(
                        i_or_w="infuse", bytes=60
                    )  # runs pump in the infuse direction then waits for target
                if args.withdraw_wait:
                    ww = pump_ultra.wait_for_target(
                        i_or_w="withdraw", bytes=60
                    )  # runs pump in the infuse direction then waits for target

        if args.PHD2000:  # if PHDULTRA is an argument from the command line
            print("You are using a PHD2000, make sure the pump is in Model 44 mode")
            print("To do this, press SET, press RS-232, and enter")
            print("Then enter address (00-99)")
            print("Then make sure BAUD RATE is 9600")
            print("And press enter one last time to save input.")
            pump_2000 = Pump2000(
                chain, args.address, name="PHD2000"
            )  # define Pump2000 class
            if args.stop:  # check if stop is an argument
                stop_pump = pump_2000.set_stop(bytes=30)
            elif args.poll:  # check if poll is an argument
                poll_pump = pump_2000.set_poll(i_or_w="infuse", bytes=30)
            elif (
                args.poll_infuse
            ):  # check if poll is an argument (exact same at normal poll for phd2000)
                poll_pump = pump_2000.set_poll(i_or_w="infuse", bytes=30)
            elif (
                args.poll_withdraw
            ):  # check if poll is an argument (exact same at normal poll for phd2000)
                poll_pump = pump_2000.set_poll(i_or_w="infuse", bytes=30)
            else:
                if args.diameter:  # if diameter is an argument, set diameter at pump
                    dia_pump = pump_2000.set_dia(args.diameter, bytes=30)
                if (
                    args.syringe_volume
                ):  # if syringe volume is an arugment, set syringe volume
                    sv_pump = pump_2000.set_syringe_volume(
                        args.syringe_volume, args.syringe_volume_units, bytes=30
                    )
                if (
                    args.target_volume
                ):  # if target volume is an arugment, set target volume
                    tv_pump = pump_2000.set_target_volume(
                        args.target_volume, args.target_volume_units, bytes=30
                    )
                else:  # if target volume is not an argument, pump needs to be set in Pump Mode
                    set_pump_mode = pump_2000.write_read("MOD PMP", 30)
                if args.infuse_rate:  # if infuse rate is an arugment, set infuse rate
                    if args.infuse_rate_units:  # also needs infuse rate units
                        ir_pump = pump_2000.set_infuse_rate(
                            args.infuse_rate, args.infuse_rate_units
                        )
                    else:  # if no units in arugments raise error
                        logging.error("%s: Need infuse rate units", pump_2000.name)
                        chain.close()
                        raise PumpError("Need infuse rate units")
                if args.infuse:
                    irun = pump_2000.set_irun(
                        bytes=30
                    )  # runs the pump in the infuse direction
                if (
                    args.withdraw
                ):  # if withdraw is an argument then set the withdraw rate, and withdraw
                    if (
                        args.withdraw_rate
                    ):  # if withdraw rate is an arugment, set infuse rate
                        if args.withdraw_rate_units:  # also needs withdraw rate units
                            wr_pump = pump_2000.set_withdraw_rate(
                                args.withdraw_rate, args.withdraw_rate_units
                            )
                        else:  # if not units send error
                            logging.error(
                                "%s: Need withdraw rate units", pump_2000.name
                            )
                            chain.close()
                            raise PumpError("Need withdraw rate units")
                    wrun = pump_2000.set_wrun(
                        bytes=30
                    )  # runs the pump in the withdraw direction
                if args.infuse_wait:  # infuse then wait for target volume
                    if args.target_volume:
                        iw = pump_2000.wait_for_target(
                            i_or_w="infuse", bytes=30
                        )  # runs the pump then waits for target
                    else:
                        logging.error("%s: Need target volume", pump_2000.name)
                        chain.close()
                        raise PumpError("Need target volume")
                if args.withdraw_wait:  # withdraw then wait for target volume
                    if args.target_volume:
                        if args.withdraw_rate:
                            if args.withdraw_rate_units:
                                wr_pump = pump_2000.set_withdraw_rate(
                                    args.withdraw_rate, args.withdraw_rate_units
                                )
                            else:
                                logging.error(
                                    "%s: Need withdraw rate units", pump_2000.name
                                )
                                chain.close()
                                raise PumpError("Need withdraw rate units")
                        ww = pump_2000.wait_for_target(
                            i_or_w="withdraw", bytes=30
                        )  # runs the pump then waits for target
                    else:
                        logging.error("%s: Need target volume", pump_2000.name)
                        chain.close()
                        raise PumpError("Need target volume")

    finally:
        chain.close()