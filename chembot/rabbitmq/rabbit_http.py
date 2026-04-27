"""
HTTP binding for RabbitMQ management and messaging APIs.

Notes
-----
- This module provides a REST-based alternative to the Pika-based AMQP client.
- It is slower and higher-latency than using Pika directly, but useful when:
    * direct AMQP connections are blocked,
    * only the RabbitMQ Management HTTP API is available,
    * or for administrative / inspection tasks.
- Many more HTTP endpoints are available via the RabbitMQ Management API;
  see RabbitMQ documentation for full reference.
"""

import base64
import json

import requests

from chembot.configuration import config


def get_vhost(
    ip: str = config.rabbit_host,
    port: int = config.rabbit_port_http,
) -> list[dict]:
    """
    Retrieve virtual host definitions from the RabbitMQ server.

    Parameters
    ----------
    ip : str
        RabbitMQ host IP or hostname.
    port : int
        RabbitMQ HTTP management port.

    Returns
    -------
    list[dict]
        List of virtual host definitions.
    """
    reply = requests.get(
        f"http://{ip}:{port}/api/vhosts",
        auth=config.rabbit_auth,
    )
    return json.loads(reply.text)


def create_exchange(
    exchange: str,
    ip: str = config.rabbit_host,
    port: int = config.rabbit_port_http,
    type_: str = "direct",
    auto_delete: bool = False,
    durable: bool = True,
    internal: bool = False,
    arguments: dict = None,
):
    """
    Create or ensure existence of an exchange via the HTTP API.
    """
    API = f"http://{ip}:{port}/api/exchanges/%2f/{exchange}"
    headers = {"content-type": "application/json"}

    pdata = {
        "type": type_,
        "auto_delete": auto_delete,
        "durable": durable,
        "internal": internal,
        "arguments": arguments if arguments is not None else {},
    }

    reply = requests.put(
        url=API,
        auth=config.rabbit_auth,
        json=pdata,
        headers=headers,
    )

    if not reply.ok:
        raise ValueError(
            f"Error creating exchange. status code: {reply.status_code}"
        )


def create_queue(
    queue: str,
    ip: str = config.rabbit_host,
    port: int = config.rabbit_port_http,
    auto_delete: bool = False,
    durable: bool = True,
    arguments: dict = None,
):
    """
    Create a queue via RabbitMQ HTTP management API.
    """
    API = f"http://{ip}:{port}/api/queues/%2f/{queue}"
    headers = {"content-type": "application/json"}

    pdata = {
        "auto_delete": auto_delete,
        "durable": durable,
        "arguments": arguments if arguments is not None else {},
    }

    reply = requests.put(
        url=API,
        auth=config.rabbit_auth,
        json=pdata,
        headers=headers,
    )

    if not reply.ok:
        raise ValueError(
            f"Error creating queue ({queue}). status code: {reply.status_code}"
        )


def delete_queue(
    queue: str,
    ip: str = config.rabbit_host,
    port: int = config.rabbit_port_http,
):
    """
    Delete a queue via RabbitMQ HTTP management API.
    """
    API = f"http://{ip}:{port}/api/queues/%2f/{queue}"
    headers = {"content-type": "application/json"}

    reply = requests.delete(
        url=API,
        auth=config.rabbit_auth,
        headers=headers,
    )

    if not reply.ok:
        raise ValueError(
            f"Error deleting queue ({queue}). status code: {reply.status_code}"
        )


def create_binding(
    queue: str,
    exchange: str = config.rabbit_exchange,
    ip: str = config.rabbit_host,
    port: int = config.rabbit_port_http,
):
    """
    Bind a queue to an exchange using a routing key.
    """
    API = f"http://{ip}:{port}/api/bindings/%2f/e/{exchange}/q/{queue}"
    headers = {"content-type": "application/json"}

    pdata = {"routing_key": exchange + "." + queue}

    reply = requests.post(
        url=API,
        auth=config.rabbit_auth,
        json=pdata,
        headers=headers,
    )

    if not reply.ok:
        raise ValueError(
            f"Error binding queue ({queue}) to exchange ({exchange}). "
            f"status code: {reply.status_code}"
        )


def publish(
    routing_key: str,
    payload: str | bytes,
    exchange: str = config.rabbit_exchange,
    ip: str = config.rabbit_host,
    port: int = config.rabbit_port_http,
):
    """
    Publish a message to an exchange using the HTTP API.

    Parameters
    ----------
    routing_key : str
        Routing key for the message.
    payload : str | bytes
        Message payload (JSON string or raw bytes).
    """
    API = f"http://{ip}:{port}/api/exchanges/%2f/{exchange}/publish"
    headers = {"content-type": "application/json"}

    pdata = {
        "properties": {},
        "routing_key": routing_key,
    }

    if isinstance(payload, bytes):
        pdata["payload_encoding"] = "base64"
        pdata["payload"] = base64.b64encode(payload).decode("ascii")
    else:
        pdata["payload_encoding"] = "string"
        pdata["payload"] = payload

    reply = requests.post(
        url=API,
        auth=config.rabbit_auth,
        json=pdata,
        headers=headers,
    )

    if not reply.ok:
        raise ValueError(
            f"Error publishing message to exchange {exchange}.\n"
            f"status code: {reply.status_code}\nreply: {reply.text}"
        )

    reply_dict = json.loads(reply.text)
    if not reply_dict.get("routed", False):
        raise ValueError(
            f"Error publishing message to exchange {exchange}. Routing invalid."
        )


def get(
    queue: str,
    ip: str = config.rabbit_host,
    port: int = config.rabbit_port_http,
    count: int = 1,
    ackmode: str = "ack_requeue_false",
    encoding: str = "auto",
    truncate: int = 50_000,
) -> list[str | bytes]:
    """
    Retrieve messages from a queue via HTTP API.

    Returns
    -------
    list[str | bytes]
        Decoded message payloads.
    """
    API = f"http://{ip}:{port}/api/queues/%2f/{queue}/get"
    headers = {"content-type": "application/json"}

    pdata = {
        "count": count,
        "ackmode": ackmode,
        "encoding": encoding,
        "truncate": truncate,
    }

    reply = requests.post(
        url=API,
        auth=config.rabbit_auth,
        json=pdata,
        headers=headers,
    )

    if not reply.ok:
        raise ValueError(
            f"Error getting message from queue {queue}.\n"
            f"status code: {reply.status_code}\nreply: {reply.text}"
        )

    reply_list = json.loads(reply.text)

    messages = []
    for message in reply_list:
        if message["payload_encoding"] == "base64":
            messages.append(base64.b64decode(message["payload"]))
        else:
            messages.append(message["payload"])

    return messages


class PaginationParameters:
    """
    Helper for pagination parameters supported by the RabbitMQ HTTP API.
    """

    def __init__(
        self,
        page: int = 1,
        page_size: int = 100,
        name: str = None,
        use_regex: bool = None,
    ):
        self.page = page
        self.page_size = page_size
        self.name = name
        self.use_regex = use_regex

    def __str__(self):
        """
        Render pagination parameters as query string.
        """
        text = "?"
        text += f"page={self.page}"
        text += f"&page_size={self.page_size}"
        if self.name is not None:
            text += f"&class_name={self.name}"
        if self.use_regex is not None:
            text += f"&use_regex={str(self.use_regex).lower()}"

        return text


def get_list_queues(
    ip: str = config.rabbit_host,
    port: int = config.rabbit_port_http,
    pagination_parameters: PaginationParameters = None,
) -> list[str]:
    """
    Retrieve list of queue names via HTTP API.
    """
    API = f"http://{ip}:{port}/api/queues"
    if pagination_parameters is not None:
        API += str(pagination_parameters)

    response = requests.get(url=API, auth=config.rabbit_auth)
    return [q["name"] for q in response.json()]


def purge_queue(
    queue: str,
    ip: str = config.rabbit_host,
    port: int = config.rabbit_port_http,
):
    """
    Remove all messages from a queue.
    """
    API = f"http://{ip}:{port}/api/queues/%2f/{queue}/contents"
    response = requests.delete(url=API, auth=config.rabbit_auth)

    if response.status_code not in (200, 204):
        raise ValueError("Purge queue failed")
