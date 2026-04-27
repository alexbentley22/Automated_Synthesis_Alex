import logging

from dash import Dash, html, dcc, Input, Output
import dash_bootstrap_components as dbc

from chembot.configuration import config
from chembot.gui.gui_data import GUIData, IDData

logger = logging.getLogger(config.root_logger_name + ".gui")


def layout_navbar(app: Dash) -> html.Div:
    """
    Create the top navigation bar for the Chembot GUI.

    Purpose
    -------
    - Provide a consistent navigation header across all GUI pages.
    - Display application branding (logo and title).
    - Offer navigation links to major sections (Home, Jobs, RabbitMQ).
    - Provide a global control button to refresh equipment data.

    Notes
    -----
    - Uses Dash Bootstrap Components for layout and styling.
    - Designed to be included once at the top-level app layout so it
      persists across page navigation.
    """

    # Main navigation bar row
    navbar = dbc.Row(
        [
            # Left column: application navbar with logo, title, and links
            dbc.Col(
                dbc.Navbar(
                    dbc.Container(
                        [
                            # Entire navbar content wrapped in a link to "/"
                            html.A(
                                dbc.Row(
                                    [
                                        # Application logo
                                        dbc.Col(
                                            html.Img(
                                                src=GUIData.LOGO,
                                                height="30px"
                                            )
                                        ),

                                        # Application title / brand
                                        dbc.Col(
                                            dbc.NavbarBrand(
                                                GUIData.navbar_title,
                                                className="ms-2"
                                            )
                                        ),

                                        # Navigation links to GUI pages
                                        dbc.Col(
                                            dbc.Nav(
                                                [
                                                    dbc.NavItem(
                                                        dbc.NavLink("Home", href="/")
                                                    ),
                                                    dbc.NavItem(
                                                        dbc.NavLink("Jobs", href="/jobs")
                                                    ),
                                                    dbc.NavItem(
                                                        dbc.NavLink("Rabbitmq", href="/rabbitmq")
                                                    ),
                                                ]
                                            )
                                        ),
                                    ],
                                    align="center",
                                    className="g-0",
                                ),
                                href="/",
                                style={"textDecoration": "none"},
                            ),
                        ]
                    ),
                    color="dark",
                    dark=True,
                )
            ),

            # Right column: global refresh button
            dbc.Col(
                dbc.Button(
                    "refresh equipment data",
                    id=IDData.REFRESH_REGISTRY,
                    color="primary",
                    className="me-1",
                ),
                width=2,
            ),
        ]
    )

    # Wrap navbar with a break for spacing below
    return html.Div([navbar, html.Br()])