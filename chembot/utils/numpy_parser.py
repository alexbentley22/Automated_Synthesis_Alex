"""
Extract reference documentation from NumPy-style docstrings.

Purpose
-------
- Parse NumPy/numpydoc-formatted docstrings into structured objects.
- Merge docstring-derived documentation with function signatures.
- Provide a machine-readable representation of API documentation.

This module is inspired by NumPy's own docstring conventions.
"""

import inspect
import types
import typing


def strip_blank_lines(line):
    """
    Remove leading and trailing blank lines from a list of strings.

    Purpose
    -------
    - Normalize docstring section content.
    - Simplify downstream parsing logic.
    """
    while line and not line[0].strip():
        del line[0]
    while line and not line[-1].strip():
        del line[-1]
    return line


class ParseError(Exception):
    """
    Custom exception raised during docstring parsing.
    """

    def __str__(self):
        """
        Provide contextual error messages including offending docstring.
        """
        message = self.args[0]
        if hasattr(self, "docstring"):
            message = f"{message} in {self.docstring!r}"
        return message


class Parameter:
    """
    Structured representation of a function or return parameter.

    Purpose
    -------
    - Store name, type, description, and default value.
    - Abstract away parsing details from consumers.
    """

    __slots__ = ("name", "type_", "description", "default")

    # Sentinel for missing defaults (mirrors inspect API)
    empty = inspect.Parameter.empty

    def __init__(
        self,
        name: str,
        type_: type | types.UnionType | empty = empty,
        description: list[str] = None,
        default=empty,
    ):
        self.name = name
        self.type_ = type_
        self.description = description
        self.default = default

    @property
    def required(self) -> bool:
        """
        Return True if the parameter has no default value.
        """
        if self.default is Parameter.empty:
            return False
        return True

    def __str__(self):
        return (
            f"Parameter(name: {self.name}, "
            f"type_: {self.type_}, "
            f"description: {self.description})"
        )

    def __repr__(self):
        return self.__str__()


class NumpyDocString:
    """
    Parsed representation of a NumPy-style docstring.

    Purpose
    -------
    - Break a docstring into named sections (Parameters, Returns, Notes, etc.).
    - Store each section in a structured, typed form.
    - Serve as the central documentation object for a callable.
    """

    # Mapping of visible section headers to attribute names
    sections = {
        "Signature": "signature",
        "Summary": "summary",
        "Extended Summary": "extended_summary",
        "Parameters": "parameters",
        "Returns": "returns",
        "Yields": "yields",
        "Receives": "receives",
        "Raises": "raises",
        "Warns": "warns",
        "Other Parameters": "other_parameters",
        "Attributes": "attributes",
        "Methods": "methods",
        "See Also": "see_also",
        "Notes": "notes",
        "Warnings": "warnings",
        "References": "references",
        "Examples": "examples",
        "index": "index",
    }

    # Lowercase lookup table for section matching
    _section_labels_lower = {k.lower(): v for k, v in sections.items()}

    def __init__(self, name: str):
        self.name = name
        self.signature: str | None = None
        self.summary: str | None = None
        self.parameters: list[Parameter] | None = None
        self.returns: list[Parameter] | None = None
        self.raises = None
        self.warns = None
        self.see_also = None
        self.notes = None
        self.warnings = None
        self.references = None
        self.examples = None

    def __str__(self):
        return self.signature

    def __repr__(self):
        return self.__str__()

    @classmethod
    def section_match(cls, text: str) -> str | None:
        """
        Determine whether a line corresponds to a known section header.
        """
        match = text.lower().strip()
        for section in cls._section_labels_lower:
            if match in section:
                return cls._section_labels_lower[section]
        return None

    def add(self, section: str, lines: list[str]):
        """
        Store parsed lines under the appropriate section.

        Parameters and returns sections are parsed into Parameter objects.
        """
        if section == "parameters":
            setattr(self, "parameters", add_parameters(lines))
        elif section == "returns":
            setattr(self, "returns", add_parameters(lines))
        else:
            if getattr(self, section) is not None:
                raise ValueError(f"'{section}' defined twice in doc string.")
            setattr(self, section, lines)


# ------------------------------------------------------------------
# Parameter parsing helpers
# ------------------------------------------------------------------

def add_parameters(lines: list[str]) -> list[Parameter]:
    """
    Convert raw parameter section lines into Parameter objects.
    """
    parameter_lines = split_parameter_lines(lines)
    return [
        parameter_line_to_parameter_object(parameter_line)
        for parameter_line in parameter_lines
    ]


def split_parameter_lines(lines: list[str]) -> list[list[str]]:
    """
    Split parameter section into blocks for each parameter.
    """
    parameters = []
    parameter_lines = []

    for line in lines:
        if line[:2] != "  ":  # new parameter
            if parameter_lines:
                parameters.append(parameter_lines)
            parameter_lines = [line]
        else:
            parameter_lines.append(line.strip())

    if parameter_lines:
        parameters.append(parameter_lines)

    return parameters


def parameter_line_to_parameter_object(lines: list[str]) -> Parameter:
    """
    Convert a single parameter block into a Parameter object.
    """
    line1 = lines[0].split(":")
    parameter = Parameter(name=line1[0])

    if len(line1) > 2:
        parameter.type_ = line1[1]

    if len(lines) > 1:
        parameter.description = lines[1:]

    return parameter


# ------------------------------------------------------------------
# Public parsing API
# ------------------------------------------------------------------

def parse_numpy_docstring_and_signature(func: typing.Callable) -> NumpyDocString:
    """
    Main entry point for extracting documentation from a callable.

    Behavior
    --------
    - Parse the NumPy-style docstring.
    - Add function signature and type annotations.
    """
    docstring = parse_numpy_docstring(func.__name__, inspect.getdoc(func))
    add_signature(func, docstring)
    return docstring


def parse_numpy_docstring(name: str, docstring: str) -> NumpyDocString:
    """
    Parse a NumPy-style docstring into structured sections.
    """
    doc = NumpyDocString(name)

    if not docstring:
        return doc

    lines = docstring.split("\n")
    current_section = "summary"
    section_lines = []

    for line in lines:
        line_ = line.strip()

        # Ignore blank lines
        if not line_:
            continue

        # Ignore underline separators
        if line_[:4] == "----":
            continue

        section_match = doc.section_match(line_)
        if section_match:
            doc.add(current_section, section_lines)
            section_lines = []
            current_section = section_match
            continue

        section_lines.append(line)

    doc.add(current_section, section_lines)
    return doc


def add_signature(function: typing.Callable, doc: NumpyDocString):
    """
    Merge function signature information into the parsed docstring object.
    """
    signature = inspect.signature(function)
    doc.signature = function.__name__ + str(signature)

    parameter_objs = get_signature_parameters(
        list(signature.parameters.values())
    )
    doc.parameters = merge_parameters(parameter_objs, doc.parameters)

    returns_objs = get_return_parameters(signature.return_annotation)
    doc.returns = merge_parameters_return(returns_objs, doc.returns)


def get_signature_parameters(
    parameter_signatures: list[inspect.Parameter],
) -> list[Parameter]:
    """
    Convert inspect.Parameter objects into Parameter representations.
    """
    parameters = []
    for p in parameter_signatures:
        if p.name == "self":
            continue
        parameters.append(
            Parameter(
                name=p.name,
                type_=p.annotation,
                default=p.default,
            )
        )
    return parameters


def get_return_parameters(
    type_: type | types.UnionType,
) -> list[Parameter] | None:
    """
    Convert a return annotation into a synthetic return Parameter.
    """
    if type_ is inspect.Parameter.empty:
        return None
    return [Parameter(name="return", type_=type_)]


def merge_parameters(
    parameters1: list[Parameter],
    parameters2: list[Parameter],
) -> list[Parameter]:
    """
    Merge parameters derived from the signature with docstring parameters.
    """
    if not parameters1:
        return parameters2
    if not parameters2:
        return parameters1

    merged = []
    for p1 in parameters1:
        for i, p2 in enumerate(parameters2):
            if p1.name == p2.name:
                p1.description = p2.description
                parameters2.pop(i)
                break
        merged.append(p1)

    merged.extend(parameters2)
    return merged


def merge_parameters_return(
    parameters1: list[Parameter],
    parameters2: list[Parameter],
) -> list[Parameter]:
    """
    Merge return type info from signature with docstring return description.
    """
    if not parameters1:
        return parameters2
    if not parameters2:
        return parameters1

    parameters1[0].description = parameters2[0].description
    return parameters1