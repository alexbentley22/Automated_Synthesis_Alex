import abc
import logging
import queue
import time

from unitpy import Quantity

from chembot.configuration import config
from chembot.utils.class_building import get_actions_list
from chembot.equipment.equipment_interface import EquipmentState, get_equipment_interface
from chembot.rabbitmq.messages import (
    RabbitMessage,
    RabbitMessageReply,
    RabbitMessageAction,
    RabbitMessageRegister,
    RabbitMessageError,
    RabbitMessageCritical,
    RabbitMessageUnRegister,
)
from chembot.rabbitmq.rabbit_core import RabbitMQConnection
from chembot.rabbitmq.watchdog import RabbitWatchdog
from chembot.equipment.continuous_event_handler import ContinuousEventHandler

logger = logging.getLogger(config.root_logger_name + ".equipment")


# ======================================================================
# High-level overview
# ----------------------------------------------------------------------
# This module defines the base `Equipment` abstraction for devices that
# participate in the Chembot system via RabbitMQ. It provides:
#
# - Lifecycle management: activate → run loop → deactivate.
# - Registration with a master controller (via RabbitMQ).
# - Message handling:
#     * RabbitMessageAction  → dispatch to methods on the equipment.
#     * RabbitMessageReply   → resolve watchdog timers and optionally queue replies.
#     * RabbitMessageError/Critical → trigger deactivation.
# - A `ContinuousEventHandler` hook to run scheduled/profiled actions in
#   the main loop without blocking.
# - Introspection of a device’s read/write actions via EquipmentInterface.
#
# Typical usage
# -------------
#   class Pump(Equipment):
#       def _activate(self): ...
#       def _deactivate(self): ...
#       def _stop(self): ...
#       def write_infuse(...): ...
#       def read_status(...): ...
#
#   pump = Pump(name="pump_one")
#   pump.activate()  # enters the infinite loop; listen/execute actions via RabbitMQ
# ======================================================================


class EquipmentConfig:
    """
    Basic safety/environmental configuration for equipment.

    Attributes
    ----------
    max_pressure, min_pressure : Quantity
        Allowed pressure bounds.
    max_temperature, min_temperature : Quantity
        Allowed temperature bounds.

    Notes
    -----
    These are not enforced automatically here; concrete equipment classes
    may consult these values in `_poll_status()` or within actions.
    """

    def __init__(
        self,
        max_pressure: Quantity = Quantity("1.1 atm"),
        min_pressure: Quantity = Quantity("0.9 atm"),
        max_temperature: Quantity = Quantity("15 degC"),
        min_temperature: Quantity = Quantity("35 degC"),
    ):
        self.max_pressure = max_pressure
        self.min_pressure = min_pressure
        self.max_temperature = max_temperature
        self.min_temperature = min_temperature


class Equipment(abc.ABC):
    """
    Base class for all Chembot equipment that communicates via RabbitMQ.

    Responsibilities
    ----------------
    - Provide a stable infinite loop (`_run`) that:
        * polls device status (`_poll_status`, optional),
        * checks watchdogs,
        * consumes/handles incoming Rabbit messages,
        * advances a `ContinuousEventHandler` if present.
    - Register/unregister with the master controller over RabbitMQ.
    - Dispatch `RabbitMessageAction` to concrete methods (read*/write*).
    - Expose a minimal read/write API for name/state/attributes.

    Lifecycle
    ---------
    `activate()`:
        - Calls `_activate()` (device-specific startup).
        - Registers with master controller.
        - Enters `_run()` infinite loop.
    Deactivation:
        - On exit, calls `_deactivate()` (device-specific teardown),
          then `_deactivate_()` to unregister and close Rabbit.

    Extension points (abstract)
    ---------------------------
    _activate(), _deactivate(), _stop()

    Attributes
    ----------
    pulse : float
        Sleep interval (seconds) used when no continuous handler is active.
    states : EquipmentState
        Alias for enum for easy access in subclasses.
    name : str
        Unique identifier for this equipment instance in the system.
    state : EquipmentState
        Current device state (initially OFFLINE; moves to STANDBY after activation).
    actions : list[str]
        Names of callable actions discovered on the instance (e.g., read..., write...).
    equipment_interface
        Structured interface describing actions/parameters parsed from the class.
    attrs : list[str]
        Attribute names returned by `read_all_attributes()`.
    update : list[str]
        Attribute names returned by `read_update()` (quick status fields).
    rabbit : RabbitMQConnection
        RabbitMQ link bound to this equipment’s logical queue.
    watchdog : RabbitWatchdog
        Tracks message replies and timeouts.
    continuous_event_handler : ContinuousEventHandler | None
        Optional scheduled/profile-driven handler.
    _message_queue : queue.Queue
        Small buffer for replies (used when `queue_it=True`).
    _deactivation_event : bool
        Flag to exit the main loop gracefully.
    """

    pulse = 0.01  # time of each loop in seconds
    states = EquipmentState

    def __init__(self, name: str, **kwargs):
        """
        Parameters
        ----------
        name : str
            Unique identifier for this equipment instance within the system.

        Notes
        -----
        - `kwargs` are set as attributes if provided (name/value pairs).
        - `actions` are discovered via `get_actions_list(self)` and the
          `equipment_interface` via `get_equipment_interface(type(self))`.
        """
        # self._reply_callback = None
        # self.action_in_progress = None

        self.name = name
        self.state: EquipmentState = EquipmentState.OFFLINE
        self.actions = get_actions_list(self)
        self.equipment_interface = get_equipment_interface(type(self))
        self.attrs = []
        self.update = ["state"]

        # managers
        self.rabbit = RabbitMQConnection(name)
        self.watchdog = RabbitWatchdog(self)
        self.continuous_event_handler: ContinuousEventHandler | None = None
        # short term storage for later processing (typically used in continuous mode)
        self._message_queue = queue.Queue(maxsize=6)

        # flags
        self._deactivation_event = False  # set to True to deactivate

        # Optional dynamic attributes (kept as-is; caller responsibility)
        if kwargs:
            for k, v in kwargs:
                setattr(self, k, v)

    # ---------------------------
    # Registration with controller
    # ---------------------------

    def _register_equipment(self):
        """
        Register this equipment with the master controller (if present).

        Protocol
        --------
        - Validates that 'master_controller' queue exists.
        - Sends RabbitMessageRegister with this instance’s interface.
        - Blocks for a reply (error_out=True).
        """
        if not self.rabbit.queue_exists("master_controller"):
            logger.critical(config.log_formatter(self, self.name, "No MasterController found on the server."))
            raise ValueError("No MasterController found on the server.")

        # update parameters
        message = RabbitMessageRegister(self.name, self.equipment_interface)
        self.rabbit.send_and_consume(message, error_out=True)

    def _unregister_equipment(self):
        """
        Unregister this equipment from the master controller (best-effort).

        Notes
        -----
        - If 'master_controller' queue does not exist, logs and returns.
        - Otherwise sends RabbitMessageUnRegister and arms a watchdog.
        """
        if not self.rabbit.queue_exists("master_controller"):
            logger.critical(
                config.log_formatter(self, self.name, "No MasterController found, so can't skip unregistering.")
            )
            return

        # update parameters
        message = RabbitMessageUnRegister(self.name)
        self.rabbit.send(message)
        self.watchdog.set_watchdog(message, 5)

    # --------------
    # Public lifecycle
    # --------------

    def activate(self):
        """
        Start the equipment’s infinite loop.

        Sequence
        --------
        1) Log activation.
        2) Call `_activate()` (subclass hook).
        3) Register with master controller.
        4) Set state to STANDBY and enter `_run()` loop.
        5) On KeyboardInterrupt or any exit, call `_deactivate()` and `_deactivate_()`.

        Note
        ----
        This call blocks until the loop exits (e.g., after `write_deactivate`).
        """
        try:
            logger.debug(config.log_formatter(self, self.name, "Activating"))
            self._activate()
            self._register_equipment()
            logger.info(config.log_formatter(self, self.name, "Activated\n" + "#" * 80 + "\n\n"))
            self.state = self.states.STANDBY

            self._run()  # infinite loop

        except KeyboardInterrupt:
            logger.info(config.log_formatter(self, self.name, "KeyboardInterrupt"))

        finally:
            logger.debug(config.log_formatter(self, self.name, "Deactivating"))

            try:
                self._deactivate()
                self._deactivate_()
            except Exception as e:
                logger.exception(str(e))
            logger.info(config.log_formatter(self, self.name, "Deactivated"))

    # -----------------
    # Main infinite loop
    # -----------------

    def _run(self):
        """
        Main / infinite event loop.

        Periodically:
          - `_poll_status()` (subclass can implement device health checks),
          - `watchdog.check_watchdogs()` to detect message timeouts,
          - consume/handle a Rabbit message (if any),
          - advance `continuous_event_handler` or sleep `pulse`.
        """
        while not self._deactivation_event:
            self._poll_status()
            self.watchdog.check_watchdogs()

            # read message
            self._process_message(self.rabbit.consume())

            # execute continuous commands
            if self.continuous_event_handler is not None:
                self.continuous_event_handler.poll(self)
            else:
                time.sleep(self.pulse)

    def _poll_status(self):
        """
        Optional hook for subclasses to implement periodic status checks.

        Examples
        --------
        - query temperatures/pressures and compare against safety limits,
        - detect hardware faults,
        - update `self.update` attributes for dashboards.
        """
        pass

    # -------------------
    # Message entry point
    # -------------------

    def _process_message(self, message: RabbitMessage):
        """
        Dispatch a consumed Rabbit message to the appropriate handler.

        Behavior
        --------
        - RabbitMessageCritical/Error → set deactivation flag.
        - RabbitMessageReply:
            * resolve watchdogs if correlation matches,
            * optionally enqueue the reply if `queue_it=True`.
        - RabbitMessageAction:
            * dispatch to the named callable on this instance,
            * send a reply with the returned value (if not None),
            * log action and reply.
        - Any other type:
            * warn and send RabbitMessageError back.

        Parameters
        ----------
        message : RabbitMessage | None
            A message or None (if no message was ready).
        """
        if message is None:
            return

        if isinstance(message, RabbitMessageCritical):
            self._deactivation_event = True

        elif isinstance(message, RabbitMessageError):
            self._deactivation_event = True

        elif isinstance(message, RabbitMessageReply):
            # Resolve watchdog if we were waiting for this correlation
            if message.id_reply in self.watchdog:
                self.watchdog.deactivate_watchdog(message)
            # Optionally queue the reply for later processing (e.g., continuous mode)
            if message.queue_it:
                try:
                    self._message_queue.put(message, timeout=1)
                except queue.Full:
                    logger.exception(
                        'Build of RabbitMessageReply in queue. '
                        f'The following message dropped: \n{message.to_str()}'
                    )

        elif isinstance(message, RabbitMessageAction):
            reply = self._execute_action(message, message.action, message.kwargs)
            if reply is not None:
                self.rabbit.send(RabbitMessageReply.create_reply(message, reply))
            logger.info(
                config.log_formatter(
                    self,
                    self.name,
                    f"Action | {message.action}: {message.kwargs}\n reply: {repr(reply)}",
                )
            )

        else:
            logger.warning("Invalid message!!" + message.to_str())
            self.rabbit.send(RabbitMessageError(self.name, f"InvalidMessage: {message.to_str()}"))

    def _execute_action(self, message: RabbitMessage, func_name: str, kwargs: dict | None):
        """
        Locate and invoke the named callable on this instance.

        Behavior
        --------
        - Look up attribute `func_name` on self.
        - If it takes only `self` (argcount==1) or kwargs is None → call without kwargs.
          Otherwise call with `**kwargs`.
        - Special case: if this is a `write_continuous_event_handler` action, attach
          the message context to the handler for downstream use.

        Returns
        -------
        Any | None
            The return value from the invoked callable, or None.

        Error handling
        --------------
        - On exception, logs and sends RabbitMessageError back (best effort).
        """
        # TODO: wrap this into a thread and use a queue
        try:
            func = getattr(self, func_name)
            if func.__code__.co_argcount == 1 or kwargs is None:  # the '1' is 'self'
                reply = func()
            else:
                reply = func(**kwargs)

            # We need the message to be added to continuous_event_handler but not sure where best to put it
            if isinstance(message, RabbitMessageAction) and \
                    message.action == self.write_continuous_event_handler.__name__:
                self.continuous_event_handler.message = message

            return reply

        except Exception as e:
            logger.exception(config.log_formatter(self, self.name, "ActionError" + message.to_str()))
            self.rabbit.send(RabbitMessageError(self.name, f"ActionError: {message.to_str()}"), check=False)

    # -----------------
    # Minimal read/write
    # -----------------

    def read_all_attributes(self) -> dict:
        """
        Return a dict of attributes (names in `self.attrs`) → current values.
        """
        return {attr: getattr(self, attr) for attr in self.attrs}

    def read_update(self) -> dict:
        """
        Return a dict of frequently-updated attributes (names in `self.update`) → values.

        Notes
        -----
        Intended for lightweight polling by dashboards or status monitors.
        """
        return {attr: getattr(self, attr) for attr in self.update}  # TODO: add sensors a

    def read_state(self) -> EquipmentState:
        """
        Get the equipment state.

        Returns
        -------
        EquipmentState
            Current state enum.
        """
        return self.state

    def read_name(self) -> str:
        """
        Return the equipment name (unique identifier).
        """
        return self.name

    def write_name(self, name: str):
        """
        Change the equipment’s name (unique identifier).

        Warning
        -------
        Renaming a running device may have implications on routing/registration.
        """
        self.name = name

    def write_deactivate(self):
        """
        Request deactivation (shut down).

        Sequence
        --------
        - Calls `_stop()` for device-specific graceful halt.
        - Sets the deactivation flag; the main loop will exit,
          and cleanup occurs in `activate()`’s finally block.
        """
        self._stop()
        self._deactivation_event = True
        # self._deactivate is called by self._run

    def write_stop(self):
        """
        Stop the current action and reset device to STANDBY.

        Behavior
        --------
        - Sets state to STANDBY.
        - If a continuous handler is active, calls its `stop()` and clears it.
        - Calls `_stop()` for device-specific halt/reset behavior.
        """
        self.state = EquipmentState.STANDBY
        if self.continuous_event_handler is not None:
            self.continuous_event_handler.stop()
            self.continuous_event_handler = None
        self._stop()

    def write_continuous_event_handler(self, event_handler: ContinuousEventHandler):
        """
        Install a `ContinuousEventHandler` to run scheduled/profiled actions.

        Parameters
        ----------
        event_handler : ContinuousEventHandler
            The handler to attach and schedule.

        Behavior
        --------
        - Assigns the handler to `self.continuous_event_handler`.
        - Sets its start time to 'now'. The main loop will then call
          `handler.poll(self)` on each iteration to drive it.
        """
        self.continuous_event_handler = event_handler
        self.continuous_event_handler.start_time = time.time()

    # -------------------------
    # Final teardown on deactivate
    # -------------------------

    def _deactivate_(self):
        """
        Common shutdown tail:
          - Set state to SHUTTING_DOWN,
          - Unregister equipment,
          - Deactivate the RabbitMQ connection.
        """
        self.state = self.states.SHUTTING_DOWN
        self._unregister_equipment()
        self.rabbit.deactivate()

    # -------------------------
    # Abstract extension points
    # -------------------------

    @abc.abstractmethod
    def _activate(self):
        """
        Device-specific startup (open ports, initialize hardware, etc.).
        """
        ...

    @abc.abstractmethod
    def _deactivate(self):
        """
        Device-specific teardown (close ports, power down, etc.).
        """
        ...

    @abc.abstractmethod
    def _stop(self):
        """
        Device-specific stop (halt an in-flight action, return to idle).
        """
        ...