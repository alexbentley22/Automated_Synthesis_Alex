import dash
from dash import dcc, html, Input, Output, State
import plotly.graph_objects as go

from scheduler.vizualization.gantt_chart import GanttChart
from chembot.scheduler.vizualization.gantt_chart_plot import (
    ConfigPlot,
    create_gantt_chart,
)


def gantt_chart_component(
    app: dash.Dash,
    data: GanttChart,
    config: ConfigPlot = None,
) -> html.Div:
    """
    Build a Dash layout containing an interactive Gantt chart
    with an optional vertical slider for large schedules.

    Purpose
    -------
    - Render a Plotly Gantt chart backed by a `GanttChart` data model.
    - Optionally include a vertical slider to window/scroll the y‑axis
      when the number of rows exceeds a configured maximum.

    Parameters
    ----------
    app : dash.Dash
        Dash application instance used to register callbacks.
    data : GanttChart
        Domain object describing rows (resources) and time intervals.
    config : ConfigPlot, optional
        Plot configuration (row limits, steps, axis behavior).

    Returns
    -------
    html.Div
        Fully constructed Dash layout for the Gantt chart component.
    """
    if config is None:
        config = ConfigPlot()

    # Layout consists of:
    #  - a left column with a vertical slider (if needed),
    #  - a main area with the Plotly graph.
    layout = html.Div(
        [
            html.Div(
                create_slider(app, data, config),
                style={
                    "float": "left",
                    "height": "450px",
                    "margin-top": "10px",
                },
            ),
            html.Div(
                [
                    dcc.Graph(
                        id="scatter-plot",
                        figure=create_gantt_chart(data, config),
                    )
                ],
                style={"margin-left": "60px"},
            ),
        ]
    )

    return layout


def create_slider(app: dash.Dash, data: GanttChart, config: ConfigPlot):
    """
    Create a vertical slider to window the Gantt chart's y-axis.

    Purpose
    -------
    - Enable scrolling through large schedules by adjusting the visible
      y-axis range of the Gantt chart.
    - Register a Dash callback that updates the figure in response to
      slider movement.

    Returns
    -------
    dcc.Slider | list
        Slider component when needed; empty list if not required.
    """
    # No slider needed if rows fit within the maximum display size
    if data.number_of_rows <= config.max_rows:
        return []

    slider = dcc.Slider(
        id="slider",
        className="slider",
        min=1,
        max=max(config.get_y_values(data.number_of_rows)),
        step=config.step,
        value=1,
        marks={
            y_: row.class_name
            for y_, row in zip(range(1, data.number_of_rows), data)
        },
        vertical=True,
    )

    @app.callback(
        Output("scatter-plot", "figure"),
        [Input("slider", "value")],
        State("scatter-plot", "figure"),
    )
    def update_scatter_plot(position: int, fig: go.Figure):
        """
        Update the Gantt chart y-axis window based on slider position.

        Behavior
        --------
        - Computes a new y-axis range centered around the slider value.
        - Updates the Plotly figure in place.
        """
        fig["layout"]["yaxis"]["range"] = get_window(
            position,
            data.number_of_rows,
            config.max_rows,
        )
        return fig

    return slider


def get_window(position: int, num_rows: int, max_rows: int) -> tuple[int, int]:
    """
    Compute the y-axis window (range) for the Gantt chart.

    Parameters
    ----------
    position : int
        Current slider position.
    num_rows : int
        Total number of rows in the Gantt chart.
    max_rows : int
        Maximum number of rows to display at once.

    Returns
    -------
    tuple[int, int]
        Lower and upper bounds for the y-axis range.
    """
    half_rows = int(max_rows / 2)

    if position <= 1 + half_rows:
        # Clamp to bottom of the chart
        return 0, max_rows + 1

    elif position > num_rows - half_rows:
        # Clamp to top of the chart
        return num_rows - max_rows, num_rows + 1

    # Center the window around the slider position
    return position - half_rows - 1, position + half_rows


def create_app(
    data: GanttChart,
    config: ConfigPlot = None,
) -> dash.Dash:
    """
    Convenience factory to create a standalone Dash app
    displaying a Gantt chart.

    Notes
    -----
    - Intended for quick visualization or debugging.
    - Call `app.run_server(debug=True)` to launch.
    """
    app = dash.Dash(__name__)
    app.layout = gantt_chart_component(app, data, config)
    return app