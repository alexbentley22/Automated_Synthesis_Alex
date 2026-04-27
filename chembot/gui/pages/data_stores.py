import logging
import time

import jsonpickle
from dash import Dash, html, dcc, Input, Output

from chembot.configuration import config
from chembot.gui.gui_data import IDData
from chembot.gui.gui_actions import (
    get_equipment_registry,
    get_equipment_attributes,
)
from chembot.equipment.equipment_interface import EquipmentRegistry


logger = logging.getLogger(config.root_logger_name + ".gui")


def layout_data_stores(app: Dash) -> html.Div:
    """
    Define and register all global Dash data stores used by the GUI.

    Purpose
    -------
    - Create persistent (session-scoped) `dcc.Store` components that hold
      backend data needed across pages and callbacks.
    - Register callbacks that populate and update these stores by querying
      the Chembot backend via RabbitMQ.
    - Centralize GUI state management so other pages can depend on these stores.

    Stores Created
    --------------
    - EQUIPMENT_REGISTRY:
        Serialized EquipmentRegistry describing all known equipment.
    - EQUIPMENT_UPDATE:
        Placeholder for incremental equipment updates (not populated here).
    - EQUIPMENT_ATTRIBUTES:
        Full attribute snapshots for each piece of equipment.
    """

    # -------------------------
    # Data store definitions
    # -------------------------

    data_stores = [
        # Stores the serialized EquipmentRegistry
        dcc.Store(
            id=IDData.EQUIPMENT_REGISTRY,
            storage_type='session',
            data="",
            modified_timestamp=time.time()
        ),

        # Stores incremental equipment updates (populated elsewhere)
        dcc.Store(
            id=IDData.EQUIPMENT_UPDATE,
            storage_type='session',
            data="",
            modified_timestamp=time.time()
        ),

        # Stores full attribute dictionaries for all equipment
        dcc.Store(
            id=IDData.EQUIPMENT_ATTRIBUTES,
            storage_type='session',
            data="",
            modified_timestamp=time.time()
        ),
    ]

    # -------------------------
    # Callbacks
    # -------------------------

    @app.callback(
        Output(IDData.EQUIPMENT_REGISTRY, "data"),
        Input(IDData.REFRESH_REGISTRY, "n_clicks")
    )
    def update_equipment_registry(_) -> str:
        """
        Update the equipment registry store when the registry-refresh
        trigger fires.

        Trigger
        -------
        - REFRESH_REGISTRY (typically a button click or timer).

        Behavior
        --------
        - Requests the latest EquipmentRegistry from the backend.
        - Returns a JSON-serialized representation.
        """
        logger.debug("updating equipment registry")
        return get_equipment_registry()

    @app.callback(
        Output(IDData.EQUIPMENT_ATTRIBUTES, "data"),
        Input(IDData.EQUIPMENT_REGISTRY, "data")
    )
    def update_equipment_attributes(data: str) -> str:
        """
        Update the equipment attributes store whenever the registry changes.

        Trigger
        -------
        - EQUIPMENT_REGISTRY data store update.

        Behavior
        --------
        1. Deserialize the EquipmentRegistry.
        2. Extract all equipment names.
        3. Query each piece of equipment for its full attribute set.
        4. Serialize and return the aggregated result.
        """
        equipment_registry: EquipmentRegistry = jsonpickle.loads(data)
        logger.debug("updating equipment attributes")
        return get_equipment_attributes(equipment_registry.equipment.keys())

    # Wrap data stores in a container div so they can be included in the layout
    return html.Div(data_stores)