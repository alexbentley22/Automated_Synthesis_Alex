import logging

import jsonpickle
from dash import Dash, html, dcc, Output, Input, State, MATCH
import dash_bootstrap_components as dbc

from chembot.configuration import config
from chembot.gui.gui_data import GUIData, IDData
import chembot.gui.gui_actions as gui_actions
from chembot.equipment.equipment_interface import (
    EquipmentRegistry,
    EquipmentInterface,
    EquipmentState,
    Action,
    ActionParameter,
)

logger = logging.getLogger(config.root_logger_name + ".gui")


class IDHome:
    """
    Collection of Dash component IDs specific to the Home page.

    Purpose
    -------
    - Centralize all ID strings used by callbacks/layouts on the Home page.
    - Avoid hard-coded IDs scattered across callbacks.
    - Enable pattern-matching callbacks via type/index dictionaries.
    """

    REFRESH_INTERVAL = "refresh_interval"
    REFRESH_DROPDOWN = "refresh_dropdown"
    EQUIPMENT_LIST = "equipment_list"
    EQUIPMENT_ITEM = "equipment_item"
    EQUIPMENT_DETAILS = "equipment_details"


# Mapping from equipment state to Bootstrap text color classes
STATUS_COLORS = {
    EquipmentState.OFFLINE: "text-dark",
    EquipmentState.PREACTIVATION: "text-dark",
    EquipmentState.STANDBY: "text-success",
    EquipmentState.SCHEDULED_FOR_USE: "text-warning",
    EquipmentState.RUNNING: "text-info",
    EquipmentState.RUNNING_BUSY: "text-info",
    EquipmentState.SHUTTING_DOWN: "text-info",
    EquipmentState.CLEANING: "text-info",
    EquipmentState.ERROR: "text-danger",
}


def equipment_layout(equipment: EquipmentInterface, update: dict):
    """
    Create the layout for a single equipment item in the equipment list.

    Displays:
    ----------
    - Equipment class name and Python class
    - Current state (with color coding)
    - Key attribute/value pairs
    - Expandable area for detailed actions and attributes

    Parameters
    ----------
    equipment : EquipmentInterface
        Equipment interface descriptor from EquipmentRegistry.
    update : dict
        Latest update data for all equipment.

    Returns
    -------
    dbc.ListGroupItem
        Clickable Bootstrap list item representing one piece of equipment.
    """
    return dbc.ListGroupItem(
        [
            # Header row: equipment name + status
            html.Div(
                [
                    html.Div(
                        [
                            html.H5(
                                equipment.class_name,
                                className="mb-1",
                                style={"text-decoration": "underline"},
                            ),
                            html.P(f"({equipment.class_})"),
                        ],
                        className="d-inline-flex w-100 justify-content-start",
                    ),
                    html.H5(
                        update[equipment.class_name]["state"].class_name
                        if update
                        else "None",
                        className=STATUS_COLORS[
                            update[equipment.class_name]["state"]
                        ]
                        if update
                        else "",
                    ),
                ],
                className="d-flex w-100 justify-content-between",
            ),

            # Summary attributes (excluding state)
            html.P(
                "||".join(
                    f"{name}: {value}"
                    for name, value in update[equipment.class_name].items()
                    if name != "state"
                )
            ),

            # Placeholder div for expandable details
            html.Div(
                id={"type": IDHome.EQUIPMENT_DETAILS, "index": equipment.class_name}
            ),
        ],
        color="primary",
        n_clicks=0,
        action=True,
        id={"type": IDHome.EQUIPMENT_ITEM, "index": equipment.class_name},
    )


def get_action_list(action: Action):
    """
    Render a single equipment Action and its I/O specification.

    Shows:
    ------
    - Action name and description
    - Input parameters
    - Output parameters
    """
    return dbc.ListGroupItem(
        [
            html.Div(
                [
                    html.P(
                        action.name,
                        className="mb-1",
                        style={"text-decoration": "underline"},
                    ),
                    html.P("  :  " + "".join(action.description)),
                ],
                className="d-inline-flex w-100 justify-content-start",
            ),
            html.P("Input:"),
            html.Div(get_parameters_layout(action.inputs)),
            html.P("Output:"),
            html.Div(get_parameters_layout(action.outputs)),
        ],
        color="secondary",
    )


def get_parameters_layout(parameters: list[ActionParameter]):
    """
    Render a list of ActionParameter objects.

    If no parameters exist, returns a placeholder string.
    """
    if not parameters:
        return "---> None"

    return [get_parameter_layout(parameter) for parameter in parameters]


def get_parameter_layout(parameter: ActionParameter):
    """
    Render a single ActionParameter.

    Displays:
    ---------
    - Parameter name
    - Accepted input types
    - Text description
    """
    return html.Div(
        [
            html.P("---> "),
            html.P(
                parameter.name,
                className="mb-1",
                style={"text-decoration": "underline"},
            ),
            html.P(f"({parameter.types}): {parameter.descriptions}"),
        ],
        className="d-inline-flex w-100 justify-content-start",
    )


def layout_home(app: Dash) -> html.Div:
    """
    Define the Home page layout and callbacks.

    Purpose
    -------
    - Display system-wide equipment status.
    - Allow expansion of equipment details and actions.
    - Periodically refresh equipment state via polling.
    """

    @app.callback(
        Output({"type": IDHome.EQUIPMENT_DETAILS, "index": MATCH}, "children"),
        Input({"type": IDHome.EQUIPMENT_ITEM, "index": MATCH}, "n_clicks"),
        [
            State({"type": IDHome.EQUIPMENT_ITEM, "index": MATCH}, "id"),
            State(IDData.EQUIPMENT_REGISTRY, "data"),
            State(IDData.EQUIPMENT_ATTRIBUTES, "data"),
        ],
    )
    def equipment_dropdown(n_clicks: int, id_: dict, data: str, attributes: str):
        """
        Expand or collapse equipment detail view when the equipment item is clicked.

        Behavior
        --------
        - Uses even/odd click count to toggle expansion.
        - Displays:
            * All current attribute values
            * All available actions with their parameters
        """
        if n_clicks % 2 == 0:
            return []

        equipment = id_["index"]
        equipment_registry: EquipmentRegistry = jsonpickle.loads(data)
        equipment_interface: EquipmentInterface = (
            equipment_registry.equipment[equipment]
        )
        attributes = jsonpickle.loads(attributes[equipment])

        actions = [get_action_list(action) for action in equipment_interface.actions]

        return [
            html.P(
                " ||  ".join(f"{name}: {value}" for name, value in attributes.items())
            ),
            dbc.ListGroup(actions),
        ]

    @app.callback(
        Output(IDHome.EQUIPMENT_LIST, "children"),
        [Input(IDData.EQUIPMENT_REGISTRY, "data"), Input(IDData.EQUIPMENT_UPDATE, "data")],
    )
    def refresh_equipment_status(data: str, update: str):
        """
        Refresh the main equipment list whenever registry or updates change.
        """
        equipment_registry: EquipmentRegistry = jsonpickle.loads(data)
        update = jsonpickle.loads(update)

        equip_layouts = [
            equipment_layout(equip, update)
            for equip in equipment_registry.equipment.values()
        ]
        return [dbc.ListGroup(equip_layouts)]

    @app.callback(
        Output(IDHome.REFRESH_INTERVAL, "interval"),
        Input(IDHome.REFRESH_DROPDOWN, "value"),
    )
    def refresh_dropdown(value: str):
        """
        Convert refresh dropdown value (seconds) to Interval component milliseconds.
        """
        return int(value) * 1000

    @app.callback(
        Output(IDData.EQUIPMENT_UPDATE, "data"),
        Input(IDHome.REFRESH_INTERVAL, "n_intervals"),
        State(IDData.EQUIPMENT_REGISTRY, "data"),
    )
    def data_equipment_update(_, data: str):
        """
        Periodic polling callback to retrieve incremental equipment updates.
        """
        equipment_registry: EquipmentRegistry = jsonpickle.loads(data)
        logger.debug("equipment update")
        return gui_actions.get_equipment_update(
            equipment_registry.equipment.keys()
        )

    # -------------------------
    # Page layout
    # -------------------------

    return html.Div(
        children=[
            # Periodic update trigger
            dcc.Interval(
                id=IDHome.REFRESH_INTERVAL,
                interval=GUIData.default_refresh_rate * 1000,
                n_intervals=-1,
            ),

            # Header row: title + refresh controls
            dbc.Row(
                [
                    dbc.Col([html.H1(children="Instrument Status")]),
                    dbc.Col(
                        html.P(
                            "Refresh Rate (sec):",
                            style={"text-align": "center"},
                        ),
                        width=1,
                    ),
                    dbc.Col(
                        [
                            dbc.Select(
                                GUIData.refresh_rates,
                                GUIData.default_refresh_rate,
                                id=IDHome.REFRESH_DROPDOWN,
                            )
                        ],
                        width=1,
                    ),
                ]
            ),

            # Equipment list container
            html.Div(id=IDHome.EQUIPMENT_LIST, children=[]),
        ]
    )
