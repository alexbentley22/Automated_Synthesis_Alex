from chembot.scheduler.vizualization.gantt_chart import GanttChart, Row, TimeBlock
from chembot.scheduler.schedule import Schedule


def schedule_to_gantt_chart(schedule: Schedule) -> GanttChart:
    """
    Convert a Scheduler `Schedule` into a `GanttChart` data model.

    Purpose
    -------
    - Bridge the gap between scheduling/execution logic and visualization.
    - Translate scheduled Events per Resource into Rows with TimeBlocks.
    - Produce a domain object ready for Gantt chart rendering (Plotly/Dash).

    Parameters
    ----------
    schedule : Schedule
        Concrete execution schedule containing resources and events.

    Returns
    -------
    GanttChart
        Structured Gantt chart data model representing the schedule.
    """
    gantt_chart = GanttChart()

    # Each resource becomes a row in the Gantt chart
    for resource in schedule.resources:
        row = Row(resource.name)

        # Each event becomes a time block within the resource row
        for event in resource.events:
            row.add_time_block(
                TimeBlock(
                    time_start=event.time_start,
                    time_end=event.time_end,
                    name=event.name,
                    hover_text=event.hover_text(),
                )
            )

        # Add the completed row to the chart
        gantt_chart.add_row(row)

    return gantt_chart