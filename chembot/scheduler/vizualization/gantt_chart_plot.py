from typing import Iterator
from datetime import datetime, timedelta

import plotly.graph_objs as go

from scheduler.vizualization.gantt_chart import GanttChart, TimeBlock


class ConfigPlot:
    """
    Plot configuration container for Gantt chart visualization.

    Purpose
    -------
    - Centralize all visual configuration parameters for Gantt charts.
    - Control rendering style (box vs bar), sizing, colors, axes, and interactivity.
    - Allow UI components to remain declarative and data-driven.

    Design Notes
    ------------
    - This class contains no plotting logic itself.
    - It only provides parameters and helper methods consumed by plotting functions.
    """

    # Supported rendering modes
    BAR = "bar"
    BOX = "box"

    def __init__(self):
        # Rendering mode for time blocks
        self.mode = "box"              # "box" or "bar"

        # Legend and hover behavior
        self.showlegend = False
        self.hover = True

        # Maximum visible rows before scrolling/windowing is required
        self.max_rows = 6

        # ------------------------------------------------------------------
        # Current time overlay styling
        # ------------------------------------------------------------------
        self.past_time_color = "rgba(200,200,200,0.4)"
        self.now_line_color = "rgba(0,0,0,0.8)"

        # ------------------------------------------------------------------
        # Global layout / window settings
        # ------------------------------------------------------------------
        self.background_color = "rgba(255,255,255,1)"
        self.show_axis = True
        self.margin = dict(l=10, r=10, b=10, t=40, pad=0)
        self.width = 1200
        self.height_per_row = 50
        self.height = None  # if None, height is computed dynamically
        self.step = 1       # spacing step between rows (do not change)
        self.x_slider_division = 10

        # ------------------------------------------------------------------
        # Bar-style rendering parameters
        # ------------------------------------------------------------------
        self.bar_line_width = 5
        self.bar_vertical_span = 0.3

        # ------------------------------------------------------------------
        # Box-style rendering parameters
        # ------------------------------------------------------------------
        self.box_line_width = 30
        self.box_width = self.step / 3

    # ------------------------------------------------------------------
    # Axis helpers
    # ------------------------------------------------------------------

    def get_y_values(self, num_rows: int) -> Iterator[int | float]:
        """
        Generate y-axis values for rows.

        Each row is assigned a numeric y position starting at 1.
        """
        return range(1, num_rows + 1, self.step)

    def get_height(self, num_rows: int) -> int:
        """
        Compute figure height based on number of rows.

        Uses a fixed per-row height unless an explicit height is provided.
        """
        if self.height is not None:
            return self.height

        return self.height_per_row * num_rows + 150

    def layout_kwargs(self, data: GanttChart) -> dict:
        """
        Construct Plotly layout keyword arguments for the Gantt chart.
        """
        return {
            "plot_bgcolor": self.background_color,
            "paper_bgcolor": self.background_color,
            "width": self.width,
            "showlegend": False,
            "height": self.get_height(data.number_of_rows),
            "margin": self.margin,
            "xaxis": self.x_axis_layout(data),
            "yaxis": self.y_axis_layout(data),
        }

    def x_axis_layout(self, data: GanttChart) -> dict:
        """
        Define layout configuration for the time (x) axis.
        """
        return {
            "visible": self.show_axis,
            "linecolor": "black",
            "linewidth": 5,
            "ticks": "outside",
            "tickwidth": 4,
            "showgrid": True,
            "gridcolor": "lightgray",
            "mirror": True,
            "type": "date",
            "range": (data.time_min, data.time_max),
            "rangeselector": self.layout_range_slider(),
            "rangeslider": dict(
                visible=True,
                bordercolor="black",
                borderwidth=3,
                thickness=0.1,
            ),
        }

    def y_axis_layout(self, data: GanttChart) -> dict:
        """
        Define layout configuration for the resource (y) axis.
        """
        return {
            "visible": self.show_axis,
            "linecolor": "black",
            "linewidth": 5,
            "ticks": "outside",
            "tickwidth": 4,
            "showgrid": True,
            "gridcolor": "lightgray",
            "mirror": True,
            "range": (1 - self.step, data.number_of_rows + self.step),
            "tickmode": "array",
            "tickvals": tuple(self.get_y_values(data.number_of_rows)),
            "ticktext": data.row_labels,
        }

    @staticmethod
    def layout_range_slider():
        """
        Define range selector buttons for the x-axis.
        """
        return dict(
            buttons=[
                dict(count=1, label="1sec", step="second", stepmode="backward"),
                dict(count=1, label="1min", step="minute", stepmode="backward"),
                dict(count=1, label="1h", step="hour", stepmode="backward"),
                dict(count=1, label="1d", step="day", stepmode="backward"),
                dict(count=1, label="1m", step="month", stepmode="backward"),
                dict(count=1, label="1y", step="year", stepmode="backward"),
                dict(step="all"),
            ],
            activecolor="#b0b0b0",
        )

    # ------------------------------------------------------------------
    # Trace helpers
    # ------------------------------------------------------------------

    def scatter_kwargs(self, text: str = None) -> dict:
        """
        Common keyword arguments for Plotly scatter traces.
        """
        kwargs = {}

        if not self.showlegend:
            kwargs["showlegend"] = False

        if self.hover and text is not None:
            kwargs["hovertext"] = text

        return kwargs

    def box_kwargs(self, text: str = None) -> dict:
        """
        Keyword arguments for box-style traces.
        """
        kwargs = {}
        if self.hover and text is not None:
            kwargs["hovertext"] = text
        return kwargs

    def current_time_kwargs(self) -> dict:
        """
        Styling for the current time overlay.
        """
        return {
            "fillcolor": self.past_time_color,
            "line": {"color": self.now_line_color},
        }


# ------------------------------------------------------------------
# Low-level drawing primitives
# ------------------------------------------------------------------

def create_bar(fig: go.Figure, time_block: TimeBlock, y: float, config: ConfigPlot):
    """Draw a bar-style time block."""
    fig.add_trace(
        go.Scatter(
            x=[
                time_block.time_start,
                time_block.time_start,
                time_block.time_start,
                time_block.time_end,
                time_block.time_end,
                time_block.time_end,
            ],
            y=[
                y - config.bar_vertical_span,
                y + config.bar_vertical_span,
                y,
                y,
                y - config.bar_vertical_span,
                y + config.bar_vertical_span,
            ],
            mode="lines",
            line=dict(color="black", width=config.bar_line_width),
            **config.scatter_kwargs(time_block.hover_text),
        )
    )


def create_line(fig: go.Figure, time_block: TimeBlock, y: float, config: ConfigPlot):
    """Draw a vertical line for instantaneous or ongoing events."""
    fig.add_trace(
        go.Scatter(
            x=(time_block.time_start, time_block.time_start),
            y=(y - config.bar_vertical_span, y + config.bar_vertical_span),
            mode="lines",
            line=dict(color="black", width=config.bar_line_width),
            **config.scatter_kwargs(time_block.hover_text),
        )
    )


def create_box(fig: go.Figure, time_block: TimeBlock, y: float, config: ConfigPlot):
    """Draw a thick horizontal line representing a duration block."""
    fig.add_trace(
        go.Scatter(
            x=(time_block.time_start, time_block.time_end),
            y=(y, y),
            mode="lines",
            line=dict(color="black", width=config.box_line_width),
            **config.scatter_kwargs(time_block.hover_text),
        )
    )


def create_box2(fig: go.Figure, time_block: TimeBlock, y: float, config: ConfigPlot):
    """Draw a filled rectangular duration block using shapes."""
    delta = (time_block.time_end - time_block.time_start) * 0.05
    fig.add_shape(
        type="rect",
        x0=time_block.time_start + delta,
        y0=y - config.box_width,
        x1=time_block.time_end - delta,
        y1=y + config.box_width,
        line=dict(color="black", width=1),
        fillcolor="black",
    )


def add_current_time(
    fig: go.Figure,
    x_min: datetime,
    x_max: datetime,
    num_rows: int,
    config: ConfigPlot,
):
    """
    Overlay a shaded region representing elapsed (past) time.
    """
    fig.add_trace(
        go.Scatter(
            x=[x_min, x_max, x_max, x_min, x_min],
            y=[0, 0, num_rows, num_rows, 0],
            fill="toself",
            **config.current_time_kwargs(),
        )
    )


def create_gantt_chart(data: GanttChart, config: ConfigPlot = None) -> go.Figure:
    """
    Main Gantt chart rendering function.

    Purpose
    -------
    - Convert a GanttChart domain object into a Plotly Figure.
    - Render each TimeBlock using the selected visual style.
    - Apply axes, layout, and current time overlays.
    """
    if config is None:
        config = ConfigPlot()

    fig = go.Figure()

    # Render time blocks row by row
    for i, row in enumerate(data, start=1):
        for time_block in row.time_blocks:
            if time_block.time_end is None:
                create_line(fig, time_block, y=i, config=config)
            else:
                if config.mode == ConfigPlot.BOX:
                    create_box2(fig, time_block, y=i, config=config)
                    create_box(fig, time_block, y=i, config=config)
                else:
                    create_bar(fig, time_block, y=i, config=config)

    # Add current time overlay if available
    if data.current_time is not None:
        add_current_time(
            fig,
            data.time_min,
            data.current_time,
            data.number_of_rows,
            config,
        )

    fig.update_layout(**config.layout_kwargs(data))
    return fig