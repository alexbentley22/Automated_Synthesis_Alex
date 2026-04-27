import uuid
from typing import Callable
import pickle

from chembot.configuration import config


class RabbitMessage:
    """
    Base class for all RabbitMQ messages in Chembot.

    Purpose
    -------
    - Define the minimal metadata required for any message:
        * unique message ID
        * source (sender)
        * destination (receiver)
    - Provide serialization (pickling) for transport over RabbitMQ.
    - Provide a readable string representation for logging and debugging.

    Notes
    -----
    - All other message types inherit from this class.
    - Messages are serialized using Python pickle with a protocol
      defined in configuration.
    """

    __slots__ = ("id_", "destination", "source")

    def __init__(self, destination: str, source: str):
        # Unique message identifier (UUID converted to int)
        self.id_: int = uuid.uuid4().int

        # RabbitMQ routing info
        self.destination = destination
        self.source = source

    def __str__(self):
        return self.to_str()

    def __repr__(self):
        return self.__str__()

    def to_bytes(self) -> bytes:
        """
        Serialize the message to bytes for RabbitMQ transport.
        """
        return pickle.dumps(self, protocol=config.pickle_protocol)

    def to_str(self) -> str:
        """
        Human-readable representation for logging.
        """
        return (
            f"\n\t{type(self).__name__}"
            f"\n\t{self.source} -> {self.destination} "
            f"\n\tid: {self.id_}"
        )


class RabbitMessageError(RabbitMessage):
    """
    Message type representing a non-critical error condition.

    Typically used when:
    - an invalid action is requested
    - an equipment-specific error occurs
    - execution fails but the system can continue operating
    """

    __slots__ = ("error",)

    def __init__(self, source: str, error: str):
        # Errors are always routed back to the master controller
        super().__init__("master_controller", source)
        self.error = error

    def to_str(self) -> str:
        return super().to_str() + f"\n\tERROR: {self.error}"


class RabbitMessageCritical(RabbitMessage):
    """
    Message type representing a critical system error.

    Purpose
    -------
    - Signal unrecoverable or unsafe conditions.
    - Typically triggers a full system shutdown by the MasterController.
    """

    __slots__ = ("error",)

    def __init__(self, source: str, error: str):
        # Critical errors are routed to the master controller
        super().__init__("master_controller", source)
        self.error = error

    def to_str(self) -> str:
        return super().to_str() + f"\n\tCritical: {self.error}"


class RabbitMessageAction(RabbitMessage):
    """
    Message representing a request to execute an action.

    Actions are how:
    - the GUI controls equipment,
    - the scheduler triggers equipment tasks,
    - the master controller calls device methods.

    Attributes
    ----------
    action : str
        Name of the method to call on the destination object.
    kwargs : dict
        Keyword arguments to pass to the method.
    id_job : int | None
        Optional job identifier (used by scheduler).
    """

    __slots__ = ("action", "kwargs", "id_job")

    def __init__(
        self,
        destination: str,
        source: str,
        action: str | Callable,
        kwargs: dict = None,
        id_job: int = None,
    ):
        super().__init__(destination, source)

        # Normalize callable -> string name
        if isinstance(action, Callable):
            action = action.__name__

        self.action = action
        self.kwargs = kwargs
        self.id_job = id_job

    def to_str(self) -> str:
        text = super().to_str()
        text += f"\n\tid_job: {self.id_job}"
        text += f"\n\taction: {self.action}"
        text += "\n\tkwargs: "
        if self.kwargs is not None:
            text += "".join(
                f"\n\t\t{k}: {repr(v)}" for k, v in self.kwargs.items()
            )

        return text


class RabbitMessageReply(RabbitMessage):
    """
    Message representing a reply to a previously sent message.

    Purpose
    -------
    - Return values from executed actions.
    - Correlate responses with original requests via `id_reply`.
    - Enable synchronous RPC-like behavior over RabbitMQ.
    """

    __slots__ = ("id_reply", "value", "queue_it")

    def __init__(
        self,
        destination: str,
        source: str,
        id_reply: int,
        value,
        queue_it: bool = False,
    ):
        super().__init__(destination, source)
        self.id_reply = id_reply
        self.value = value
        self.queue_it = queue_it

    def to_str(self) -> str:
        return (
            super().to_str()
            + f"\n\tid_reply: {self.id_reply}"
            + f"\n\tvalue: {repr(self.value)}"
        )

    @staticmethod
    def create_reply(message: RabbitMessage, value):
        """
        Convenience constructor for replying to an existing message.

        Automatically swaps source/destination and copies message ID.
        """
        return RabbitMessageReply(
            destination=message.source,
            source=message.destination,
            id_reply=message.id_,
            value=value,
        )


class RabbitMessageRegister(RabbitMessage):
    """
    Message sent by equipment to register itself with the MasterController.

    Contains:
    - source equipment name
    - equipment interface metadata (actions, attributes, etc.)
    """

    def __init__(self, source: str, equipment_interface):
        super().__init__("master_controller", source)
        self.equipment_interface = equipment_interface


class RabbitMessageUnRegister(RabbitMessage):
    """
    Message sent by equipment to unregister itself from the MasterController.
    """

    def __init__(self, source: str):
        super().__init__("master_controller", source)