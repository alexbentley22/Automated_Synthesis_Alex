"""
Extension to `rabbit_http.py` to support sending and receiving typed
RabbitMessage objects over the RabbitMQ HTTP API.

Purpose
-------
- Provide a thin RPC-style layer on top of the RabbitMQ Management HTTP API.
- Serialize RabbitMessage objects to bytes for transport.
- Poll queues and reconstruct replies as RabbitMessageReply objects.
- Serve as a fallback transport when direct AMQP (pika) access is unavailable.

Notes
-----
- This module is slower than the pika-based AMQP implementation.
- Intended for GUI backends, restricted environments, or debugging.
"""

import json
import time
import logging
import pickle

from chembot.configuration import config
from chembot.rabbitmq.messages import RabbitMessage, RabbitMessageReply
from chembot.rabbitmq.rabbit_http import publish, get

logger = logging.getLogger(config.root_logger_name + ".rabbitmq")


def write_message(message: RabbitMessage):
    """
    Send a RabbitMessage using the HTTP API.

    Assumptions
    -----------
    - Uses a topic exchange.
    - Message destination determines routing key.

    Parameters
    ----------
    message : RabbitMessage
        Message object to send.
    """
    publish(message.destination, message.to_bytes())
    logger.debug(
        config.log_formatter(
            "RabbitMQConnection",
            "http",
            "Message sent:" + message.to_str(),
        )
    )


def read_message(queue: str, time_out: float = 1) -> str | bytes:
    """
    Poll a queue until a message is received or timeout expires.

    Parameters
    ----------
    queue : str
        Queue name to read from.
    time_out : float
        Maximum time to wait (seconds).

    Returns
    -------
    str | bytes
        Raw message payload:
        - str   -> JSON-encoded message
        - bytes -> pickled RabbitMessage

    Raises
    ------
    ValueError
        If timeout expires before a message arrives.
    """
    time_out = time.time() + time_out

    # Poll repeatedly until timeout
    while time.time() < time_out:
        reply = get(queue)  # returns list[str | bytes]

        if reply:
            reply = reply[0]
            logger.debug(
                config.log_formatter(
                    "RabbitMQConnection",
                    "http",
                    "Message received:\n\t"
                    + str(reply)[: min([100, len(str(reply))])],
                )
            )
            return reply

    raise ValueError(f"Timeout error on queue: {queue}")


def re_create_message(message: str | bytes) -> RabbitMessageReply:
    """
    Convert a raw HTTP message payload back into a RabbitMessageReply.

    Behavior
    --------
    - bytes  -> unpickle to RabbitMessageReply
    - str    -> parse JSON

    Parameters
    ----------
    message : str | bytes
        Raw message payload.

    Returns
    -------
    RabbitMessageReply
    """
    if isinstance(message, bytes):
        return pickle.loads(message)

    return json.loads(message)


def write_and_read_message(
    message: RabbitMessage,
    time_out: float = 1,
) -> str | bytes:
    """
    Send a message and return the raw reply payload.

    Notes
    -----
    - This function does NOT reconstruct a RabbitMessageReply.
    - It is useful when the caller wants to inspect raw data.
    """
    write_message(message)
    return read_message(message.source, time_out)


def read_create_message(
    queue: str,
    time_out: float = 1,
) -> RabbitMessageReply:
    """
    Read a message from a queue and reconstruct it as a RabbitMessageReply.
    """
    reply = read_message(queue, time_out)
    return re_create_message(reply)


def write_read_create_message(
    message: RabbitMessage,
    time_out: float = 1,
) -> RabbitMessageReply:
    """
    Full RPC helper:
        send message -> wait -> deserialize reply.

    This is the HTTP equivalent of `send_and_consume()` in the pika-based
    RabbitMQConnection.
    """
    write_message(message)
    reply = read_message(message.source, time_out)
    return re_create_message(reply)