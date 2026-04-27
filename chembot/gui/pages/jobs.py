import logging

import plotly.graph_objs as go
from dash import Dash, html, dcc, Output, Input, State, MATCH
import dash_bootstrap_components as dbc

from chembot.configuration import config

logger = logging.getLogger(config.root_logger_name + ".gui")


class IDJobs:
    """
    Collection of Dash component IDs used on the Jobs page.

    Purpose
    -------
    - Centralize string identifiers to avoid duplication and typos.
    - Make it easier to refactor or extend the Jobs page without
      searching for hard-coded IDs across callbacks and layouts.
    """

    JOB_QUEUE = "job_queue"
    REFRESH_TIMELINE = "refresh_timeline"
    TIMELINE = "timeline"


# ------------------------------------------------------------------
# Example timeline creation (currently disabled / placeholder)
# ------------------------------------------------------------------
# The commented code below shows how a Gantt-style timeline could be
# generated using Plotly. It is left as a reference for future work.
#
# def create_timeline() -> go.Figure:
#     df = pd.DataFrame([
#         dict(Equipment="Serial", Start='2023-01-01', Finish='2023-02-28', Job="expt_1"),
#         dict(Equipment="Red_LED", Start='2023-03-05', Finish='2023-04-15', Job="expt_1"),
#         dict(Equipment="Mint_LED", Start='2023-02-20', Finish='2023-05-30', Job="expt_2")
#     ])
#
#     fig = px.timeline(df, x_start="Start", x_end="Finish", y="Equipment", color="Job")
#     fig.update_yaxes(autorange="reversed")
#     return fig


def layout_jobs(app: Dash) -> html.Div:
    """
    Define the Jobs page layout and callbacks.

    Purpose
    -------
    - Provide a dedicated page for job management and visualization.
    - Display a (future) job queue and execution timeline.
    - Support manual refresh of the timeline via a button-triggered callback.
    """

    @app.callback(
        Output(IDJobs.TIMELINE, "children"),
        Input(IDJobs.REFRESH_TIMELINE, "n_clicks")
    )
    def layout_timeline(_):
        """
        Update the job timeline display when the refresh button is clicked.

        Notes
        -----
        - Currently returns an empty Plotly graph placeholder.
        - Intended to be replaced with a real timeline figure
          (e.g., via Plotly Gantt/timeline charts).
        """
        return [dcc.Graph()]  # figure=create_timeline()

    # -------------------------
    # Page layout
    # -------------------------

    return html.Div(
        children=[
            # Header row with title and refresh button
            dbc.Row(
                [
                    dbc.Col([html.H1(children='Instrument Status')]),
                    dbc.Col(width=1),
                    dbc.Col(
                        [
                            dbc.Button(
                                "refresh timeline",
                                id=IDJobs.REFRESH_TIMELINE,
                                color="primary",
                                className="me-1",
                            )
                        ],
                        width=1,
                    ),
                ]
            ),

            # Placeholder for job queue visualization/list
            html.Div(id=IDJobs.JOB_QUEUE, children=[]),

            # Timeline section header
            html.H1(children='Timeline'),

            # Container updated by the callback above
            html.Div(id=IDJobs.TIMELINE, children=[]),
        ]
    )