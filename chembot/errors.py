from typing import Protocol

from chembot import logger


class EquipmentObject(Protocol):
    """
    Minimal protocol for objects that can appear in error messages.

    Requirements
    ------------
    - `id_ : int` — a stable identifier used in logs and error text.

    Notes
    -----
    Any equipment/device class that participates in these error types
    can implement this attribute to produce better, more actionable messages.
    """
    id_: int


class ChemBotError(Exception):
    """
    Base exception for application-specific errors in Chembot.

    Purpose
    -------
    - Provides a common ancestor for all domain exceptions so callers
      can catch `ChemBotError` to handle *only* known, application-level
      failures (distinct from generic programming errors).
    """
    ...


class EquipmentError(ChemBotError):
    """
    Exception representing a failure related to a specific equipment object.

    Behavior
    --------
    - Formats the error message as:
        "<ClassName> (id: <id_>): <text>"
    - Logs the error message through the shared `chembot.logger` at ERROR level.

    Parameters
    ----------
    obj : EquipmentObject
        The equipment-like object involved in the error. Must provide `id_`.
    text : str
        Human-readable description of the problem.

    Examples
    --------
        raise EquipmentError(pump, "Flow sensor not responding")

    Notes
    -----
    - Logging occurs at construction time so that creating the exception
      leaves a trace even if it propagates uncaught.
    """

    def __init__(self, obj: EquipmentObject, text: str):
        self.text = f"{type(obj).__name__} (id: {obj.id_}): " + text
        logger.error(self.text)

    def __str__(self):
        return self.text


# Example specialized error skeletons 
# -------------------------------------------------------------------
# class CommunicationError(ChemBotError):
#     def __init__(self, text: str):
#         self.text = text
#         logger.error(text)
#     def __str__(self):
#         return self.text
#
# class PumpError(ChemBotError):
#     def __init__(self, obj: ErrorObject, text: str):
#         self.text = text
#         logger.error(text)
#     def __str__(self):
#         return self.text


class PumpFlowRateError(ChemBotError):
    """
    Exception raised when a requested or measured pump flow rate is invalid.

    Typical causes
    --------------
    - Requested flow is out of device limits.
    - Flow unit/dimensionality mismatch (after validation).
    - Real-time flow reading indicates a dangerous or impossible value.

    Parameters
    ----------
    text : str
        Description of the flow-rate problem. The message is logged at ERROR.

    Notes
    -----
    - Like `EquipmentError`, the message is logged upon construction so
      telemetry contains failures even if they are caught elsewhere.
    """

    def __init__(self, text: str):
        self.text = text
        logger.error(text)

    def __str__(self):
        return self.text