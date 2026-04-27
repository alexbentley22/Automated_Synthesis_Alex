from typing import Iterable

import jsonpickle

from chembot.gui.gui_data import GUIData
from chembot.rabbitmq.messages import RabbitMessageAction
from chembot.rabbitmq.rabbit_http_messages import write_read_create_message
from chembot.master_controller.master_controller import MasterController
from chembot.equipment.equipment import Equipment
from chembot.equipment.equipment_interface import EquipmentRegistry


def get_equipment_registry() -> str:
    """
    Request the full equipment registry from the MasterController and
    return it as a JSON-serializable string.

    Workflow
    --------
    1. Create a RabbitMQ action message addressed to the MasterController.
    2. Request invocation of `read_equipment_registry`.
    3. Receive the EquipmentRegistry object as the reply value.
    4. Serialize the registry using jsonpickle so it can be sent to the GUI.

    Returns
    -------
    str
        JSON-encoded representation of the EquipmentRegistry.
    """
    reply = write_read_create_message(
        RabbitMessageAction(
            destination="chembot." + MasterController.name,
            source=GUIData.name,
            action=MasterController.read_equipment_registry.__name__
        )
    )

    equipment_registry: EquipmentRegistry = reply.value
    return jsonpickle.dumps(equipment_registry)


def get_equipment_attributes(equipments: Iterable) -> str:
    """
    Retrieve *all attributes* for a collection of equipment objects.

    Parameters
    ----------
    equipments : Iterable
        Iterable of equipment names (strings).

    Workflow
    --------
    - For each equipment name:
        1. Send a RabbitMQ request to that equipment.
        2. Invoke the `read_all_attributes` action.
        3. Collect the returned attribute dictionary.
    - Serialize the aggregated data into JSON using jsonpickle.

    Returns
    -------
    str
        JSON-encoded mapping:
            {
                equipment_name: { attribute_name: value, ... },
                ...
            }
    """
    data = {}
    for equipment in equipments:
        reply = write_read_create_message(
            RabbitMessageAction(
                destination="chembot." + equipment,
                source=GUIData.name,
                action=Equipment.read_all_attributes.__name__
            )
        )
        data[equipment] = reply.value

    return jsonpickle.dumps(data)


def get_equipment_update(equipments: Iterable) -> str:
    """
    Retrieve *incremental updates* for a collection of equipment objects.

    Parameters
    ----------
    equipments : Iterable
        Iterable of equipment names (strings).

    Workflow
    --------
    - For each equipment:
        1. Send a RabbitMQ request to invoke `read_update`.
        2. Collect the update payload (typically a diff or recent changes only).
    - Serialize the consolidated update dictionary to JSON.

    Returns
    -------
    str
        JSON-encoded mapping:
            {
                equipment_name: update_payload,
                ...
            }
    """
    data = {}
    for equipment in equipments:
        reply = write_read_create_message(
            RabbitMessageAction(
                destination="chembot." + equipment,
                source=GUIData.name,
                action=Equipment.read_update.__name__
            )
        )
        data[equipment] = reply.value

    return jsonpickle.dumps(data)


def do_action(equipment: str, action: str, kwargs):
    """
    Dispatch an arbitrary action to a specific equipment and return the result.

    Parameters
    ----------
    equipment : str
        Name of the target equipment (suffix to 'chembot.').
    action : str
        Name of the method/action to invoke on the equipment.
    kwargs : dict or Any
        Arguments passed to the action.

    Workflow
    --------
    - Construct a RabbitMessageAction addressed to the equipment.
    - Send the message and wait for a reply.
    - Return the result from the reply.

    Returns
    -------
    Any
        The value returned by the equipment action.
    """
    message = RabbitMessageAction(
        destination="chembot." + equipment,
        source=GUIData.name,
        action=action,
        kwargs=kwargs
    )
    reply = write_read_create_message(message)

    return reply.value