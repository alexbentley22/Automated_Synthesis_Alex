import abc
import enum
import types
import inspect
from typing import Iterable, Callable
import functools
import logging

from unitpy import Unit, Quantity

from chembot.configuration import config
import chembot.utils.numpy_parser as numpy_parser


logger = logging.getLogger(config.root_logger_name + ".equipment_interface")


# ======================================================================
# High-level overview
# ----------------------------------------------------------------------
# This module provides a reflection-based “equipment interface” system:
#
# - Parse equipment classes for public actions (methods that start with
#   `read...` or `write...`), and build a structured interface describing:
#     * action name and type (READ/WRITE),
#     * inputs/outputs with types, ranges, units, and default values,
#     * a registry to track equipment interfaces by name.
#
# - It supports parsing of Numpy-style docstrings/signatures using
#   `numpy_parser.parse_numpy_docstring_and_signature(func)` to extract
#   parameter metadata (types, descriptions, ranges, units).
#
# - It also provides range validators and type validators to ensure
#   parameters follow expected constraints (numerical ranges, categorical
#   options, and unit dimensionalities using unitpy).
#
# Typical usage
# -------------
#   iface = get_equipment_interface(SomeEquipmentClass)
#   action = iface.get_action("write_flow_rate")
#   for param in action.inputs:
#       param.validate(value)
#
# The EquipmentRegistry can store/get these interfaces by a given name.
# ======================================================================


class EquipmentState(enum.Enum):
    """
    Canonical equipment lifecycle/state codes.

    These are useful for orchestration logic and UI; they do not enforce
    any behavior by themselves.
    """
    OFFLINE = 0          # used for equipment that was online at some point but gone
    PREACTIVATION = 1    # used to announce it coming online
    STANDBY = 2
    SCHEDULED_FOR_USE = 3
    RUNNING = 4
    RUNNING_BUSY = 5     # will not accept writes in this state
    SHUTTING_DOWN = 6
    CLEANING = 7
    ERROR = 8


class ActionType(enum.Enum):
    """
    Two high-level action categories, derived from method name:
      - READ:  methods whose names start with 'read'
      - WRITE: everything else (e.g., 'write', 'set', etc.)
    """
    READ = 0
    WRITE = 1


class ParameterRange(abc.ABC):
    """
    Abstract base class representing allowed values for a parameter.

    Concrete implementations:
      - NumericalRangeContinuous: open interval (min, max)
      - NumericalRangeDiscretized: closed interval with step [min:step:max]
      - CategoricalRange: finite set of options (ints/floats/strings)
    """

    def __repr__(self):
        return self.__str__()

    @abc.abstractmethod
    def validate(self, value) -> bool:
        """
        Validate that `value` falls within the allowed range; raise on error.

        Returns
        -------
        bool
            Implementations may return True (success), but the contract is to
            raise ValueError for invalid values.
        """
        ...


class NumericalRangeContinuous(ParameterRange):
    """
    Continuous numerical range (open interval): (min_, max_).

    Validation enforces strict inequality: min_ &lt; value &lt; max_
    """

    def __init__(self, min_: float | int, max_: float | int):
        self.min_ = min_
        self.max_ = max_

    def __str__(self):
        return f"[{self.min_}:{self.max_}]"

    def validate(self, value):
        if not (self.min_ < value < self.max_):
            raise ValueError(f"{type(self).__name__}: Outside Range: [{self.min_}:{self.max_}]")


class NumericalRangeDiscretized(ParameterRange):
    """
    Discretized numerical range with step: [min_:step:max_], inclusive.

    Validation enforces:
      - min_ &lt;= value &lt;= max_
      - value aligns with step relative to min_ (modular test)
    """

    def __init__(self, min_: float | int, max_: float | int, step: int | float | None = None):
        self.min_ = min_
        self.max_ = max_
        self.step = step

    def __str__(self):
        text = f"[{self.min_}:"
        if self.step is not None:
            text += f"{self.step}:"
        return text + f"{self.max_}]"

    def validate(self, value):
        if not (self.min_ <= value <= self.max_) or (value % self.step) != (self.min_ % self.step):
            raise ValueError(f"{type(self).__name__}: Outside Range: [{self.min_}:{self.step}:{self.max_}]")


class CategoricalRange(ParameterRange):
    """
    Categorical set of allowed options (ints/floats/strings).
    """

    def __init__(self, options: Iterable[int] | Iterable[float] | Iterable[str]):
        self.options = options

    def __str__(self):
        return str(self.options)

    def validate(self, value):
        if value not in self.options:
            raise ValueError(f"{type(self).__name__}: Invalid option. Expected: {self.options}")


class ActionParameter:
    """
    Describes a single parameter (input or output) for an Action.

    Attributes
    ----------
    name : str
        Parameter name.
    type_ : type | types.UnionType | empty
        Expected Python type (or Union), or inspect.Parameter.empty.
    descriptions : str
        Human-readable description parsed from docstring.
    range_ : ParameterRange | None
        Optional value constraints (continuous/discretized/categorical).
    unit : str | empty
        Unit string for unitpy (e.g., 'ml/min'); dimensionality is validated.
    default : Any | empty
        Default value if any.

    Methods
    -------
    validate(value)
        Validate type, units, and range for an input argument.
    """

    empty = inspect.Parameter.empty

    def __init__(self,
                 name: str,
                 type_: type | types.UnionType | empty,
                 descriptions: str = "",
                 range_: ParameterRange | None = None,
                 unit: str = empty,
                 default=empty,
                 ):
        self.name = name
        self.descriptions = descriptions
        self.type_ = type_
        self.range_ = range_
        self.unit = unit
        self.default = default

    def __str__(self):
        """
        Render as a concise signature fragment: 'name: type = default'
        (Only includes parts that are present.)
        """
        text = self.name
        if self.type_ is not self.empty:
            if not hasattr(self.type_, "__origin__"):
                text += ": " + self.type_.__name__
            else:
                text += ": " + str(self.type_)
        if self.default is not self.empty:
            text += " = " + str(self.default)
        return text

    def __repr__(self):
        return self.__str__()

    @property
    def required(self) -> bool:
        """
        Whether the parameter is required.

        Note
        ----
        The current logic returns False when no default is set. If you expect
        parameters *without* defaults to be required (typical), this property
        may need revisiting in future refactor.
        """
        if self.default is self.empty:
            return False
        return True

    def validate(self, value):
        """
        Validate a value against the parameter's type, unit, and range.

        Raises
        ------
        TypeError
            If type does not match, or unitpy Quantity is not provided when unit is required.
        ValueError
            If unit dimensionality mismatches or value is outside allowed range.
        """
        # Type validation (including Unions and simple list[T] handling)
        validate_type(self.type_, value)

        # Unit validation (if specified)
        if self.unit is not self.empty:
            if not isinstance(value, Quantity):
                raise TypeError(f"Received: {type(value)} || Expected: Quantity")
            if Unit(self.unit).dimensionality != value.dimensionality:
                raise ValueError(f"Wrong unit dimensionality. "
                                 f"\nReceived: {value.dimensionality} || Expected: {Unit(self.unit).dimensionality} "
                                 f"({self.unit})")
            value = value.to(self.unit)

        # Range validation (if provided)
        if self.range_ is not self.empty:
            self.range_.validate(value)


class Action:
    """
    A parsed equipment action (method), with inputs/outputs and metadata.

    Attributes
    ----------
    name : str
        Action (method) name.
    description : str
        Short summary parsed from docstring.
    type_ : ActionType
        READ if name starts with 'read'; otherwise WRITE.
    inputs : list[ActionParameter]
        Input parameter definitions.
    outputs : list[ActionParameter]
        Output parameter definitions.

    Properties
    ----------
    required_inputs : list[ActionParameter]
        Subset of `inputs` that are considered required (per `ActionParameter.required`).
    """

    def __init__(self,
                 name: str,
                 description: str = "",
                 inputs: list[ActionParameter] = None,
                 outputs: list[ActionParameter] = None
                 ):
        self.name = name
        self.description = description
        if name.startswith("read"):
            self.type_ = ActionType.READ
        else:
            self.type_ = ActionType.WRITE
        self.inputs = inputs
        self.outputs = outputs

    def __str__(self):
        return self.name + f"({''.join(str(i) for i in self.inputs)}) -&gt; {''.join(str(i) for i in self.outputs)}"

    def __repr__(self):
        return self.__str__()

    @property
    def required_inputs(self) -> list[ActionParameter]:
        return [action for action in self.inputs if action.required]


class EquipmentInterface:
    """
    Represents a fully parsed interface for an equipment class.

    Attributes
    ----------
    class_ : type
        The equipment class.
    actions : list[Action]
        All parsed actions (read/write methods).

    Properties
    ----------
    class_name : str
        Class name for convenience.
    action_names : set[str]
        Set of action names (strings) contained in this interface.

    Methods
    -------
    get_action(name: str) -> Action
        Look up an action by name; raises if not found.
    """

    def __init__(self, class_, actions: list[Action]):
        self.class_ = class_
        self.actions = actions

    def __str__(self):
        return self.class_.__name__ + f"|| " + str(len(self.actions))

    def __repr__(self):
        return self.__str__()

    @property
    def class_name(self) -> str:
        return self.class_.__name__

    @property
    def action_names(self) -> set[str]:
        return {action.name for action in self.actions}

    def get_action(self, name: str):
        """
        Retrieve a single action by exact name.

        Raises
        ------
        ValueError
            If the action name does not exist in this interface.
        """
        for action in self.actions:
            if action.name == name:
                return action

        raise ValueError(f"Action ({name}) not found in EquipmentInterface ({self.class_}).")

    # def data_row(self) -&gt; dict:
    #     return {"class_name": self.class_name, "class": self.class_, "actions": len(self.actions)}


class EquipmentRegistry:
    """
    Registry mapping a logical equipment name -> EquipmentInterface.

    Useful when many equipment classes are available and we need to look
    up an interface by name for orchestration, validation, or UI.
    """

    def __init__(self):
        self.equipment: dict[str, EquipmentInterface] = dict()

    def register(self, name: str, equipment_interface: EquipmentInterface):
        """
        Register an existing EquipmentInterface under a given name.
        """
        self.equipment[name] = equipment_interface

    def register_equipment(self, name: str, equipment):
        """
        Introspect an equipment `class` to build its interface and register it.

        Parameters
        ----------
        name : str
            Registry key to store this interface under.
        equipment : type
            The equipment class (not instance).
        """
        equipment_interface = get_equipment_interface(equipment)
        self.register(name, equipment_interface)

    def unregister(self, name: str):
        """
        Remove an equipment interface from the registry, if present.
        """
        try:
            del self.equipment[name]
        except KeyError:
            logger.error(f"Error unregistering '{name}'. Not in registry.")


#######################################################################################################################
# Reflection helpers: parse classes and methods into interfaces
#######################################################################################################################

@functools.lru_cache
def get_equipment_interface(class_: type) -> EquipmentInterface:
    """
    Given an Equipment class, construct an EquipmentInterface via reflection.

    Steps
    -----
    1) Find all public methods whose names start with 'read' or 'write'.
    2) Parse each method's numpy-style docstring/signature for parameter metadata.
    3) Build Action objects and wrap them in an EquipmentInterface.

    Caching
    -------
    Results are cached by class to avoid repeated parsing.

    Raises
    ------
    ValueError
        If a method's docstring/parameters cannot be parsed.
    """
    funcs = get_class_functions(class_)
    actions = []
    for func in funcs:
        try:
            actions.append(get_action(getattr(class_, func)))
        except Exception as e:
            raise ValueError(f"Exception raise while parsing: {class_.__name__}.{func}") from e

    return EquipmentInterface(class_, actions)


def get_class_functions(class_: type) -> list[str]:
    """
    Return a list of method names for the given class that look like actions:
    names starting with 'read' or 'write' and are callable.
    """
    funcs = []
    for func in dir(class_):
        if callable(getattr(class_, func)) and (func.startswith("read") or func.startswith("write")):
            funcs.append(func)
    return funcs


def get_action(func: Callable) -> Action:
    """
    Build an Action definition from a function/method by parsing its docstring/signature.

    Uses `numpy_parser.parse_numpy_docstring_and_signature(func)` to extract:
      - summary (description)
      - parameters (name, type, description, range, unit)
      - returns   (same structure as parameters)

    Returns
    -------
    Action
        The structured action definition.
    """
    docstring = numpy_parser.parse_numpy_docstring_and_signature(func)
    inputs_ = parse_parameters(docstring.parameters)
    outputs_ = parse_parameters(docstring.returns)
    return Action(func.__name__, docstring.summary, inputs_, outputs_)


def parse_parameters(list_: list[numpy_parser.Parameter] | None) -> list[ActionParameter]:
    """
    Convert parsed numpy_parser.Parameter objects into ActionParameter instances.
    """
    if list_ is None:
        return []

    results = []
    for parms in list_:
        description, range_, unit = parse_description(parms.description)
        results.append(
            ActionParameter(
                parms.name,
                get_type(parms.type_),
                description,
                range_,
                unit
            )
        )

    return results


def parse_description(text: list[str]) -> list[str, ParameterRange | None, str | None]:
    """
    Parse a parameter description list into:
      [description_text, range (ParameterRange or empty), unit (str or empty)]

    Simple convention:
      - lines starting with 'range' specify allowed values (see parse_range)
      - lines starting with 'unit'  specify a unit string for unitpy
      - any other line contributes to the freeform description
    """
    result = ["", ActionParameter.empty, ActionParameter.empty]  # [description, range, unit]

    if text is None:
        return result

    for line in text:
        line = line.strip()
        if line.startswith("range"):
            result[1] = parse_range(line)
        elif line.startswith("unit"):
            result[2] = line
        else:
            result[0] += line

    return result


def parse_range(text: str) -> ParameterRange | None:
    """
    Parse a range line into a ParameterRange.

    Supported syntaxes
    ------------------
    - Numerical continuous:   range: [min:max]
    - Numerical discretized:  range: [min:step:max]
    - Categorical (numbers):  range: 1,2,3 or 1.0,2.5,3.0
    - Categorical (strings):  range: 'opt1','opt2'  (quotes required)
    """
    if not text:
        return None

    text = text.replace("range:", "").replace(" ", "").replace("[", "").replace("]", "")

    # If quotes are present, treat as categorical strings
    if "'" in text or '"' in text:
        return CategoricalRange(text.replace("'", "").replace('"', "").split(","))

    # Otherwise parse numerical styles
    return parse_numerical_range(text)


def parse_numerical_range(text: str) -> NumericalRangeContinuous | NumericalRangeDiscretized | CategoricalRange:
    """
    Parse numerical range text into a specific ParameterRange subclass.

    Cases
    -----
    - No colons     → comma-separated categorical list of numerics (or None)
    - One colon     → continuous [min:max]
    - Two colons    → discretized [min:step:max]
    """
    count_collen = text.count(":")

    if count_collen == 0:
        options = text.split(",")
        options_numerical = []
        for op in options:
            try:
                num = number_to_int_or_float(op)
            except ValueError:
                if "None" in op:
                    num = None
                else:
                    raise ValueError(f"Invalid doc-sting range. \ntext: {text}")
            options_numerical.append(num)
        return CategoricalRange(options_numerical)

    if count_collen == 1:
        text = text.split(":")
        return NumericalRangeContinuous(number_to_int_or_float(text[0]), number_to_int_or_float(text[1]))

    if count_collen == 2:
        text = text.split(":")
        return NumericalRangeDiscretized(
            min_=number_to_int_or_float(text[0]),
            max_=number_to_int_or_float(text[2]),
            step=number_to_int_or_float(text[1])
        )

    raise ValueError(f"Invalid doc-sting range. \ntext: {text}")


def number_to_int_or_float(number: str) -> int | float:
    """
    Convert a numeric string to int if integral, else float.
    """
    num = float(number)
    if num == int(num):
        return int(num)
    return num


# Map of Python builtin type names -> actual types (for string-to-type resolution)
import builtins
builtin_types = {d: getattr(builtins, d) for d in dir(builtins) if isinstance(getattr(builtins, d), type)}


def get_type(type_: str | type) -> type | types.UnionType:
    """
    Resolve a docstring-declared type name (string) into a concrete Python type.

    Resolution order
    ----------------
    1) If already a `type` or `types.UnionType`, return as is.
    2) Look up in Python builtins (e.g., 'int', 'float', 'list', etc.).
    3) Resolve known domain-specific types by name (Quantity, Syringe, etc.).
    4) Otherwise raise TypeError for unknown types.

    Notes
    -----
    - Extend this function when introducing new domain types referenced by name
      in your Numpy-style docstrings.
    """
    if isinstance(type_, type) or isinstance(type_, types.UnionType):
        return type_

    if type_ in builtin_types:
        return builtin_types[type_]
    if type_ == "Quantity":
        return Quantity

    if type_ == "RampFlowRate":
        from chembot.equipment.pumps.harvard_apparatus_syringe_pump import RampFlowRate
        return RampFlowRate

    if type_ == "Syringe":
        from chembot.equipment.pumps.syringes import Syringe
        return Syringe

    if type_ == "HarvardPumpStatusMessage":
        from chembot.equipment.pumps.harvard_apparatus_syringe_pump import HarvardPumpStatusMessage
        return HarvardPumpStatusMessage

    if type_ == "HarvardPumpVersion":
        from chembot.equipment.pumps.harvard_apparatus_syringe_pump import HarvardPumpVersion
        return HarvardPumpVersion

    if type_ == "ValvePosition":
        from chembot.equipment.valves.valve_configuration import ValvePosition
        return ValvePosition

    # TODO: make more general
    raise TypeError(f"Type not known: {type_}; it needs to be added.")


def validate_type(type_: type, value):
    """
    Validate that `value` matches the expected Python type (including simple generics).

    Supported cases
    ---------------
    - Plain types: `int`, `float`, `str`, custom classes, etc.
    - `list[T]`  : Ensures value is a list and first element is instance of T.
                   (Simplistic; extend as needed.)
    - Basic Union types (via `types.UnionType`) are handled upstream in `get_type`.

    Raises
    ------
    TypeError
        If the value does not conform to the expected type pattern.
    """
    error = TypeError(f"Expected type:{type_}\nReceived type: {type(value)} ({value})")

    if hasattr(type_, "__origin__"):
        outer_layer = type_.__origin__
        inner_layer = type_.__args__
        if outer_layer is list:
            if not isinstance(value, list):
                raise error
            if not isinstance(value[0], inner_layer[0]):  # handle full list and  ...
                raise error
        # TODO: dict, tuple

    else:
        if not isinstance(value, type_):
            raise error