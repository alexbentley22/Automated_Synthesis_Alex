from chembot.scheduler.event import Event
from chembot.scheduler.job import Job, JobConcurrent, JobSequence


class ConfigMermaidFlowchart:
    """
    Configuration container for Mermaid flowchart generation.

    Purpose
    -------
    - Centralize all styling options for Mermaid diagrams.
    - Control colors for jobs, events, arrows, and background.
    - Adjust visual emphasis (e.g., trigger arrows).

    Notes
    -----
    - This class only defines appearance, not structure.
    - Styling is injected into Mermaid via `pre_text` and `post_text`.
    """

    def __init__(self):
        # Node colors
        self.color_event = "#005f73"          # blue
        self.color_job_sequence = "#758E4F"   # green
        self.color_job_concurrent = "#9b2226" # red

        # Global appearance
        self.color_background = "#404e4d"
        self.color_arrow = "#ca6702"

        # Special arrow styling (e.g., triggers)
        self.color_trigger_arrows = "#758E4F"
        self.trigger_arrow_size = 6

    @property
    def pre_text(self) -> str:
        """
        Mermaid header block defining global theme and variables.

        This is prepended before the graph definition.
        """
        return "\n".join(
            [
                "%%{",
                "\tinit: {",
                "\t\t'theme': 'base',",
                "\t\t'themeVariables': {",
                f"\t\t\t'background': '{self.color_background}',",
                "\t\t\t'fontFamily': 'arial',",
                "\t\t\t'primaryColor': '#005f73',",
                "\t\t\t'primaryTextColor': '#ffffff',",
                "\t\t\t'primaryBorderColor': '#000000',",
                f"\t\t\t'lineColor': '{self.color_arrow}',",
                "\t\t\t'tertiaryColor': '#fdf0d5',",
                "\t\t\t'tertiaryTextColor': '#000000'",
                "\t\t}",
                "\t}",
                "}%%",
            ]
        )

    @property
    def post_text(self) -> str:
        """
        Mermaid class definitions for node styling.

        This is appended after the graph definition.
        """
        return "".join(
            [
                f"\tclassDef job_sequence fill:{self.color_job_sequence}\n",
                f"\tclassDef job_concurrent fill:{self.color_job_concurrent}\n",
                f"\tclassDef event fill:{self.color_event}\n",
            ]
        )


class MermaidFlowchartData:
    """
    Mutable builder object for constructing a Mermaid flowchart.

    Purpose
    -------
    - Accumulate Mermaid syntax text incrementally.
    - Assign stable, unique labels to Jobs and Events.
    - Track arrows and their styles.

    Design Notes
    ------------
    - Uses object IDs to ensure consistent node references.
    - Supports limited recursion depth for nested jobs.
    """

    def __init__(self, depth: int = 1, config: ConfigMermaidFlowchart = None):
        self.depth = depth
        self.config = config if config is not None else ConfigMermaidFlowchart()

        # Internal counters
        self.index = 0
        self.arrow_index = 0

        # Mappings from object ID to Mermaid labels
        self.label_map = {}

        # Mermaid graph text fragments
        self.text = "graph LR;\n"
        self.text_style = ""

        # Optional signal-to-arrow mappings (future use)
        self.signal_map = {}

    @property
    def final_text(self) -> str:
        """
        Concatenate all Mermaid text components into a final diagram string.
        """
        return "\n".join(
            (self.config.pre_text, self.text, self.text_style, self.config.post_text)
        )

    def get_label(self, obj: str | Event | Job) -> str:
        """
        Return (or create) a Mermaid node label for an object.

        Behavior
        --------
        - Strings are passed through directly.
        - Jobs and Events receive numbered labels with class annotations.
        """
        if isinstance(obj, str):
            return obj

        if obj.id_ in self.label_map:
            return self.label_map[obj.id_]

        index = self.index
        self.index += 1

        # Base label
        self.label_map[obj.id_] = f"{index}({obj.name})"

        # Annotate by type for styling
        if isinstance(obj, JobConcurrent):
            return f"{index}({obj.name}):::job_concurrent"
        if isinstance(obj, JobSequence):
            return f"{index}({obj.name}):::job_sequence"
        if isinstance(obj, Event):
            return f"{index}({obj.name}):::event"

        return f"{index}({obj.name})"

    def add_arrow(
        self,
        obj1: Event | Job | str,
        obj2: Event | Job | str,
        text: str = None,
    ) -> int:
        """
        Add a directed arrow between two nodes.

        Returns
        -------
        int
            Index of the arrow (used for styling).
        """
        self.text += (
            "\t"
            + self.get_label(obj1)
            + self.get_arrow_symbol(text)
            + self.get_label(obj2)
            + ";\n"
        )
        self.arrow_index += 1
        return self.arrow_index - 1

    def add_line_style(self, arrow_index: int):
        """
        Apply a custom style to an existing arrow.
        """
        self.text_style += (
            f"\tlinkStyle {arrow_index} "
            f"stroke:{self.config.color_trigger_arrows},"
            f"stroke-width:{self.config.trigger_arrow_size}px;\n"
        )

    def get_arrow_symbol(self, text: str = None):
        """
        Return Mermaid arrow syntax, optionally with a label.
        """
        if text is None:
            return " --> "
        return f" -- {text} --> "


def generate_job_flowchart(job: Job, depth: int = 5) -> str:
    """
    Generate a Mermaid flowchart representation for a Job.

    Parameters
    ----------
    job : Job
        Root job to visualize.
    depth : int
        Maximum recursion depth for nested jobs.

    Returns
    -------
    str
        Complete Mermaid diagram text.
    """
    data = MermaidFlowchartData(depth)
    loop_generate_mermaid_flowchart(job, data)
    return data.final_text


def loop_generate_mermaid_flowchart(
    job: Job,
    data: MermaidFlowchartData,
    depth: int = 0,
):
    """
    Recursively traverse a Job hierarchy and add Mermaid arrows.

    Behavior
    --------
    - Adds arrows from the current job to its events.
    - Recurses into nested jobs up to the configured depth.
    """
    if depth > data.depth:
        return

    for event in job.events:
        data.add_arrow(job, event)

        if hasattr(event, "events"):
            loop_generate_mermaid_flowchart(event, data, depth + 1)