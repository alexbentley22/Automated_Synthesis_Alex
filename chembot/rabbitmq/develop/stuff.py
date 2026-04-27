"""
Minimal example of using the RabbitMQ Management HTTP API to publish a message.

Reference
---------
https://funprojects.blog/2019/11/08/rabbitmq-rest-api/

Purpose
-------
- Demonstrate how to send a message to a RabbitMQ exchange using HTTP
  instead of a native AMQP client (e.g. pika).
- Acts as a simple proof-of-concept or manual test script.
- Useful for experimentation, debugging, or learning the REST API.

Notes
-----
- This script assumes the RabbitMQ Management plugin is enabled.
- Uses default credentials (guest/guest) and localhost.
- Not intended for production use as-is.
"""

# importing the requests library
import requests
import json

# ---------------------------------------------------------------------
# RabbitMQ HTTP API endpoint
# ---------------------------------------------------------------------
# This endpoint publishes a message to the exchange "chembot"
# in the default virtual host (%2f encodes "/").
API_ENDPOINT = "http://127.0.0.1:15672/api/exchanges/%2f/chembot/publish"

# ---------------------------------------------------------------------
# HTTP headers
# ---------------------------------------------------------------------
# RabbitMQ Management API expects JSON payloads
headers = {'content-type': 'application/json'}

# ---------------------------------------------------------------------
# Payload to be sent to the API
# ---------------------------------------------------------------------
# This structure mirrors what RabbitMQ's /publish endpoint expects:
# - routing_key: determines which queues receive the message
# - payload: message body (JSON string in this example)
# - payload_encoding: how the payload should be interpreted
pdata = {
    'properties': {},
    'routing_key': 'chembot.deep_red',
    'payload': '{"action": "set_power", "value": 10}',
    'payload_encoding': 'string'
}

# ---------------------------------------------------------------------
# HTTP POST request
# ---------------------------------------------------------------------
# Send the message using HTTP basic authentication.
# Default RabbitMQ credentials are ('guest', 'guest').
r = requests.post(
    url=API_ENDPOINT,
    auth=('guest', 'guest'),
    json=pdata,
    headers=headers
)

# ---------------------------------------------------------------------
# Response handling
# ---------------------------------------------------------------------
# Print the raw response from the RabbitMQ server.
pastebin_url = r.text
print("Response :%s" % pastebin_url)