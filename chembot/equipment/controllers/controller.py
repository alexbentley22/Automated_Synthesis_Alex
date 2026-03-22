from typing import Callable

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


class Controller:
    """
    Minimal RabbitMQ-driven controller wrapper.

    Purpose
    -------
    - Provides a RabbitMQ connection + watchdog to integrate a controlling
      function (`func`) into your message bus.
    - This class mirrors the lifecycle entrypoint (`activate`) of Equipment,
      but is intentionally much lighter: it’s designed for orchestrators,
      supervisors, or coordinating services that don’t expose read/write actions
      like devices do.

    Notes
    -----
    - This skeleton assumes the existence of `_activate()`, `_deactivate()`,
      `_deactivate_()`, `_register_equipment()`, `_run()`, and some attributes
      like `self.name`, `self.states`, and `self.state`. Those are **not**
      implemented in this snippet. In your real codebase, either:
        * implement these methods/attributes on Controller, or
        * subclass Controller and provide them there, or
        * mix in a common base that provides the lifecycle and metadata
          (e.g., a shared “RuntimeBase” similar to `Equipment`).
    - If this controller should also declare an interface (for discoverability)
      or handle incoming `RabbitMessageAction`s, you can add behavior similar
      to the `Equipment` base (dispatch, replies, etc.).
    """

    def __init__(self, name: str, func: Callable):
        """
        Parameters
        ----------
        name : str
            Logical name for this controller; used for RabbitMQ routing/queue.
        func : Callable
            The controlling function or entrypoint this controller wraps.
            You may call this within `_run()` or other lifecycle points.
        """
        # RabbitMQ connection bound to this controller's queue
        self.rabbit = RabbitMQConnection(name)
        # Watchdog manager to correlate requests/replies and catch timeouts
        self.watchdog = RabbitWatchdog(self)
        # The controller's main callable (supplied by the user)
        self.func = func

        # (Optional) If your runtime expects these, set them here or in a subclass:
        # self.name = name
        # self.states = EquipmentState
        # self.state = self.states.OFFLINE
        # self.attrs = []
        # self.update = ["state"]

    def activate(self):
        """
        Start the controller's lifecycle and enter the main loop.

        Sequence (expected, see Notes in class docstring)
        -------------------------------------------------
        1) Log activation.
        2) Call `_activate()` (controller-specific startup).
        3) Call `_register_equipment()` if this controller should register/discover.
        4) Set state to `STANDBY`.
        5) Enter `_run()` infinite loop.
        6) On `KeyboardInterrupt` or exit, call `_deactivate()` and `_deactivate_()`.

        Important
        ---------
        - This method references `logger`, `self.name`, `self.states`, and `self.state`,
          as well as `_activate/_deactivate/_deactivate_/_register_equipment/_run`.
          Ensure those exist in your concrete implementation.
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