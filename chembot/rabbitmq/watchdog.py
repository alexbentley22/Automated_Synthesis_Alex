import time
import logging
from typing import Protocol

from chembot.configuration import config
from chembot.rabbitmq.messages import (
    RabbitMessage,
    RabbitMessageReply,
    RabbitMessageError,
)
from chembot.rabbitmq.rabbit_core import RabbitMQConnection

logger = logging.getLogger(config.root_logger_name + ".watchdog")


class ParentInterfaceWatchdog(Protocol):
    """
    Structural typing interface for any object that can own a RabbitWatchdog.

    Purpose
    -------
    - Define the minimal attributes required by RabbitWatchdog.
    - Avoid tight coupling to specific parent classes.

    Required Attributes
    -------------------
    name : str
        Logical name of the parent (used for logging and error reporting).
    rabbit : RabbitMQConnection
        RabbitMQ connection used to send error messages.
    """
    name: str
    rabbit: RabbitMQConnection


class WatchdogEvent:
    """
    Internal data container representing a single watchdog timer.

    Purpose
    -------
    - Track a message awaiting a reply.
    - Store timeout information and optional validation callbacks.
    - Trigger an error if the reply is not received in time.

    Attributes
    ----------
    id_ : int
        ID of the message being monitored.
    delay : int | float
        Allowed time (seconds) before watchdog triggers.
    expect_reply : str | None
        Optional expected value for the reply.
    reply_callback : callable | None
        Optional function invoked when the reply is received.
    warn_time : float
        Absolute timestamp when watchdog should trigger.
    """

    def __init__(
        self,
        id_: int,
        delay: int | float,
        expect_reply: str | None = None,
        reply_callback: callable = None,
    ):
        self.id_ = id_
        self.delay = delay
        self.expect_reply = expect_reply
        self.reply_callback = reply_callback

        # Absolute time when watchdog should fire
        self.warn_time = time.time() + delay

    def __str__(self):
        """Compact string used in error messages."""
        return f"{self.id_}: {self.warn_time}"


class RabbitWatchdog:
    """
    Watchdog manager for RabbitMQ message replies.

    Purpose
    -------
    - Monitor outstanding RabbitMessages that expect replies.
    - Detect missing or delayed replies.
    - Emit RabbitMessageError messages when timeouts occur.
    - Optionally validate reply content or invoke callbacks.

    Typical Usage
    -------------
    1. Send a RabbitMessage.
    2. Call `set_watchdog()` for that message.
    3. When a reply arrives, call `deactivate_watchdog()`.
    4. Periodically call `check_watchdogs()` in the main loop.
    """

    def __init__(self, parent: ParentInterfaceWatchdog):
        """
        Parameters
        ----------
        parent : ParentInterfaceWatchdog
            Owning object (typically MasterController or equipment).
        """
        self.parent = parent

        # Active watchdogs keyed by message ID
        self.watchdogs: dict[int, WatchdogEvent] = {}

    def __contains__(self, item: int) -> bool:
        """
        Support `id_ in watchdog` syntax.
        """
        return item in self.watchdogs.keys()

    def set_watchdog(
        self,
        message: RabbitMessage,
        delay: int | float,
        expected_reply=None,
        reply_callback: callable = None,
    ):
        """
        Register a watchdog for a message expecting a reply.

        Parameters
        ----------
        message : RabbitMessage
            Message to monitor.
        delay : int | float
            Timeout in seconds before watchdog triggers.
        expected_reply : Any, optional
            Expected reply value (strict equality check).
        reply_callback : callable, optional
            Function called upon successful reply.
        """
        self.watchdogs[message.id_] = WatchdogEvent(
            message.id_, delay, expected_reply, reply_callback
        )

    def deactivate_watchdog(self, message: RabbitMessageReply):
        """
        Deactivate watchdog upon receiving a matching reply.

        Behavior
        --------
        - Validates that a watchdog exists for this reply.
        - Optionally checks reply value against expected value.
        - Invokes callback if provided.
        - Removes watchdog from active list.
        """
        if message.id_reply not in self.watchdogs:
            logger.exception(
                f"Reply id({message.id_reply}) not in watchdog list "
                f"for parent '{self.parent.name}'."
            )
            return

        watchdog = self.watchdogs[message.id_reply]

        if (
            watchdog.expect_reply is not None
            and message.value != watchdog.expect_reply
        ):
            raise ValueError(
                f"Reply message has wrong value. "
                f"message_id: ({message.id_})\n"
                f"value: {message.value}\n"
                f"expected value: {watchdog.expect_reply}"
            )

        if watchdog.reply_callback:
            watchdog.reply_callback(message)

        del self.watchdogs[message.id_reply]

    def check_watchdogs(self):
        """
        Scan all active watchdogs and trigger errors if expired.

        Behavior
        --------
        - Sends a RabbitMessageError to the parent when a watchdog fires.
        - Removes expired watchdogs to prevent repeated triggers.
        """
        now = time.time()
        del_watchdog = []

        for id_, watchdog in self.watchdogs.items():
            if now > watchdog.warn_time:
                del_watchdog.append(id_)
                self.parent.rabbit.send(
                    RabbitMessageError(
                        self.parent.name,
                        f"Watchdog triggered for message {watchdog}",
                    )
                )

        # Cleanup triggered watchdogs
        for id_ in del_watchdog:
            del self.watchdogs[id_]