class IDData:
    """
    Centralized collection of string IDs used by the GUI to identify
    different data stores, signals, or update channels.

    Purpose
    -------
    - Provide a single source of truth for IDs referenced across the GUI.
    - Avoid hard-coded string literals scattered throughout callbacks,
      layouts, and data-handling logic.
    - Ensure consistent naming when communicating between front-end
      components and back-end update mechanisms.

    Notes
    -----
    - These values are typically used as IDs for Dash components
      (e.g., dcc.Store IDs or callback identifiers).
    - Changing a value here affects all places where the ID is referenced.
    """

    REFRESH_REGISTRY = "data_refresh_registry"
    EQUIPMENT_REGISTRY = "data_equipment_registry"
    EQUIPMENT_UPDATE = "data_equipment_update"
    EQUIPMENT_ATTRIBUTES = "data_equipment_attributes"
    TIMELINE = "data_timeline"


class GUIData:
    """
    Container for global, static configuration values used by the Chembot GUI.

    Purpose
    -------
    - Define GUI-wide constants such as names, branding, and refresh behavior.
    - Centralize values that are shared across multiple GUI modules and pages.
    - Make it easy to update UI behavior or appearance without touching
      individual components.

    Attributes
    ----------
    name : str
        Logical name of the GUI client (used for RabbitMQ identification).
    pulse : float
        Small timing constant often used for animation, polling, or debounce.
    LOGO : str
        Path to the logo asset displayed in the navigation bar.
    navbar_title : str
        Text displayed in the GUI navigation bar.
    default_refresh_rate : int
        Default interval (in seconds) for periodic updates.
    refresh_rates : tuple[int]
        Allowed refresh intervals selectable in the GUI.
    """

    # Logical name of the GUI (used as a RabbitMQ queue / source identifier)
    name = "GUI"

    # Small timing constant (seconds), often used in periodic callbacks
    pulse = 0.01

    # Path to logo asset for the navigation bar
    LOGO = "assets/icon-research-catalysis-white.svg"

    # Title text shown in the navigation bar
    navbar_title = "chembot"

    # Default refresh interval (seconds)
    default_refresh_rate = 30

    # Available refresh intervals (seconds) exposed to the user
    refresh_rates = (1, 2, 5, 10, 30)
