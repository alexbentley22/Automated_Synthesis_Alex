import logging

from dash import Dash, html
import dash
import dash_bootstrap_components as dbc

from chembot.configuration import config
from chembot.gui.gui_data import GUIData
from chembot.rabbitmq.rabbit_http import create_queue, create_binding, delete_queue

# Page layouts (Dash Pages API)
from chembot.gui.pages.navbar import layout_navbar
from chembot.gui.pages.data_stores import layout_data_stores
from chembot.gui.pages.home import layout_home
from chembot.gui.pages.rabbitmq import layout_rabbit
from chembot.gui.pages.jobs import layout_jobs

logger = logging.getLogger(config.root_logger_name + ".gui")


class GUI:
    """
    Top-level Dash application wrapper for the Chembot web GUI.

    Purpose
    -------
    - Create and configure the Dash application instance.
    - Register all GUI pages (home, RabbitMQ monitor, job control, etc.).
    - Manage lifecycle hooks that create and tear down a dedicated RabbitMQ
      queue for GUI communication.
    - Provide a single `activate()` entry point to launch the web server.

    Design Notes
    ------------
    - Uses the Dash Pages API (`use_pages=True`) to support multi-page routing.
    - Uses a Bootstrap theme (DARKLY) for consistent styling.
    - Designed to be used as a context manager so RabbitMQ resources are
      automatically cleaned up on exit.
    """

    # Logical GUI name (used as RabbitMQ queue name)
    name = GUIData.name

    def __init__(self, debug: bool = True):
        """
        Parameters
        ----------
        debug : bool
            If True, run Dash in debug mode (auto-reload, verbose errors).
        """
        self.debug = debug

        # Create Dash application with Bootstrap styling and page support
        self.app = Dash(
            __name__,
            use_pages=True,
            external_stylesheets=[dbc.themes.DARKLY]
        )

        # Register layout and pages
        self._register_pages()

    # -------------------------
    # Context manager support
    # -------------------------

    def __enter__(self):
        """
        Enter GUI runtime context.

        Creates a dedicated RabbitMQ queue and binding so the GUI can:
        - receive status updates
        - publish control actions
        """
        self._create_rabbitmq_connection()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Exit GUI runtime context.

        Ensures RabbitMQ resources are cleaned up even if an exception occurs.
        """
        self._close_rabbitmq_connection()

    # -------------------------
    # RabbitMQ lifecycle
    # -------------------------

    def _create_rabbitmq_connection(self):
        """
        Create a GUI-specific RabbitMQ queue and bind it to the
        configured Chembot exchange.
        """
        create_queue(self.name)
        create_binding(self.name, config.rabbit_exchange)

    def _close_rabbitmq_connection(self):
        """
        Delete the GUI-specific RabbitMQ queue.

        Prevents orphaned queues when the GUI shuts down.
        """
        delete_queue(self.name)

    # -------------------------
    # Application control
    # -------------------------

    def activate(self):
        """
        Start the Dash server.

        Notes
        -----
        - This call is blocking.
        - Intended to be called inside a `with GUI(...) as gui:` block.
        """
        self.app.run_server(debug=self.debug)

    # -------------------------
    # Page and layout registration
    # -------------------------

    def _register_pages(self):
        """
        Register common layout components and individual pages.

        Layout Structure
        ----------------
        - Navbar (persistent across all pages)
        - Global data stores (Dash dcc.Store equivalents)
        - Page container for routed views
        """

        # Shared layout for all pages
        self.app.layout = html.Div(
            [
                layout_navbar(self.app),
                layout_data_stores(self.app),
                dash.page_container
            ]
        )

        # Individual routed pages
        dash.register_page("home", path='/', layout=layout_home(self.app))
        dash.register_page("rabbitmq", path='/rabbitmq', layout=layout_rabbit(self.app))
        dash.register_page("jobs", path='/jobs', layout=layout_jobs(self.app))