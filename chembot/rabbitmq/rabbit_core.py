import logging
import pickle

import pika
import pika.exceptions

# Silence overly verbose pika logs
logging.getLogger("pika").setLevel(logging.WARNING)

from chembot.configuration import config
from chembot.rabbitmq.messages import RabbitMessage, RabbitMessageReply
from chembot.rabbitmq.rabbit_http import get_list_queues, purge_queue

logger = logging.getLogger(config.root_logger_name + ".rabbitmq")


def get_rabbit_channel():
    """
    Create and return a RabbitMQ channel connected to the configured broker.

    Purpose
    -------
    - Establish a blocking connection to the RabbitMQ server.
    - Declare the main Chembot exchange if it does not already exist.
    - Return a ready-to-use channel object.

    Returns
    -------
    pika.channel.Channel
        A configured RabbitMQ channel.
    """
    credentials = pika.PlainCredentials(
        config.rabbit_username,
        config.rabbit_password,
    )
    parameters = pika.ConnectionParameters(
        config.rabbit_host,
        config.rabbit_port,
        '/',
        credentials,
        heartbeat=600,
    )
    connection = pika.BlockingConnection(parameters)
    channel = connection.channel()

    # Declare topic-based exchange used by Chembot
    channel.exchange_declare(
        exchange=config.rabbit_exchange,
        exchange_type='topic',
    )

    return channel


def create_queue(channel, topic):
    """
    Create (or recreate) a queue for a given topic and bind it to the exchange.

    Behavior
    --------
    - If a queue with the same name already exists, it is purged.
    - The queue is declared with auto-delete enabled.
    - The queue is bound to the Chembot exchange using a topic routing key.
    """
    if queue_exists(topic):
        purge_queue(topic)

    result = channel.queue_declare(topic, auto_delete=True)
    channel.queue_bind(
        exchange=config.rabbit_exchange,
        queue=result.method.queue,
        routing_key=config.rabbit_exchange + "." + topic,
    )


def queue_exists(queue_name: str) -> bool:
    """
    Check whether a queue exists on the RabbitMQ broker.

    Parameters
    ----------
    queue_name : str
        Name of the queue to check.

    Returns
    -------
    bool
        True if the queue exists, False otherwise.
    """
    queues = get_list_queues()
    return queue_name in queues


class RabbitMQConnection:
    """
    High-level wrapper around a RabbitMQ channel for Chembot communication.

    Purpose
    -------
    - Encapsulate queue creation, message sending, and message consumption.
    - Provide RPC-style send-and-reply behavior.
    - Handle message serialization/deserialization.
    - Integrate logging and basic error handling.

    Each instance represents one logical endpoint (topic).
    """

    def __init__(self, topic: str):
        """
        Initialize a RabbitMQ connection bound to a specific topic/queue.

        Parameters
        ----------
        topic : str
            Logical name of this endpoint (used as queue name).
        """
        self.topic = topic

        # Establish channel and create queue
        self.channel = get_rabbit_channel()
        create_queue(self.channel, self.topic)

        # Passive declare to fetch queue metadata
        self.queue = self.channel.queue_declare(queue=topic, passive=True)

        logger.debug(
            config.log_formatter(self, self.topic, "Rabbit connection established.")
        )

    # -------------------------
    # Message consumption
    # -------------------------

    @staticmethod
    def queue_exists(queue_name: str) -> bool:
        """Check if a RabbitMQ queue exists."""
        return queue_exists(queue_name)

    def consume(
        self,
        timeout: int | float = 0.000_001,
        error_out: bool = False,
    ) -> RabbitMessage | None:
        """
        Consume a single message from the queue.

        Parameters
        ----------
        timeout : float
            Inactivity timeout in seconds.
        error_out : bool
            If True, raise an error when no message is available.

        Returns
        -------
        RabbitMessage | None
            The decoded RabbitMessage, or None if none available.
        """
        for method, properties, body in self.channel.consume(
            queue=self.topic,
            auto_ack=True,
            inactivity_timeout=timeout,
        ):
            if body is None:
                if error_out:
                    raise ValueError("No message to consume.")
                return None

            return self._process_message(body)

    def _process_message(self, body: bytes) -> RabbitMessage | None:
        """
        Deserialize raw message bytes into a RabbitMessage.

        Parameters
        ----------
        body : bytes
            Pickled message payload.

        Returns
        -------
        RabbitMessage | None
        """
        try:
            message = pickle.loads(body)
            logger.debug(
                config.log_formatter(
                    self, self.topic, "Message received:" + message.to_str()
                )
            )
            return message
        except Exception:
            logger.exception(
                config.log_formatter(
                    self, self.topic, "Received message caused Exception."
                )
            )

    # -------------------------
    # Message sending
    # -------------------------

    def send(self, message: RabbitMessage, check: bool = True):
        """
        Send a RabbitMessage to its destination queue.

        Parameters
        ----------
        message : RabbitMessage
            Message object to send.
        check : bool
            If True, verify destination queue exists before sending.

        Raises
        ------
        ValueError
            If destination queue does not exist and `check=True`.
        """
        if check and not queue_exists(message.destination):
            logger.error(
                config.log_formatter(
                    self, self.topic, "Queue does not exist yet:" + message.destination
                )
            )
            raise ValueError("Queue does not exist yet:" + message.destination)

        try:
            self.channel.basic_publish(
                exchange=config.rabbit_exchange,
                routing_key=config.rabbit_exchange + "." + message.destination,
                body=message.to_bytes(),
                properties=pika.BasicProperties(delivery_mode=2),
            )
            logger.debug(
                config.log_formatter(
                    self, self.topic, "Message sent:" + message.to_str()
                )
            )
        except Exception as e:
            logger.error(
                config.log_formatter(
                    self, self.topic, "Message not sent:" + message.to_str()
                )
            )
            raise e

    def send_and_consume(
        self,
        message: RabbitMessage,
        timeout: int | float = 0.3,
        error_out: bool = False,
    ) -> RabbitMessageReply | None:
        """
        Send a message and wait synchronously for a reply.

        This provides RPC-like behavior over RabbitMQ.
        """
        self.send(message)
        try:
            return self.consume(timeout, error_out)
        except ValueError:
            raise ValueError(
                f"No reply received from message: {message.id_}"
            )

    # -------------------------
    # Shutdown
    # -------------------------

    def deactivate(self):
        """
        Close the queue consumer and release channel resources.
        """
        self.channel.basic_cancel(self.topic)
        logger.debug(
            config.log_formatter(self, self.topic, "Rabbit connection closed.")
        )