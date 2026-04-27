import logging
import traceback
from typing import Any

from chembot.configuration import config
from chembot.equipment.equipment_interface import (
    EquipmentRegistry,
    EquipmentInterface,
    ActionParameter,
)
from chembot.equipment.continuous_event_handler import ContinuousEventHandler
from chembot.scheduler.event import Event
from chembot.scheduler.schedule import Schedule
from chembot.scheduler.resource import Resource
from chembot.scheduler.submit_result import JobSubmitResult

logger = logging.getLogger(config.root_logger_name + ".master_controller")


def validate_schedule(schedule: Schedule, registry: EquipmentRegistry, result: JobSubmitResult):
    """
    Perform full validation of a Schedule before job submission.

    Purpose
    -------
    - Ensure all referenced equipment exists and supports requested actions.
    - Validate all event arguments against action definitions.
    - Detect overlapping events on the same resource.
    - Accumulate any validation errors into JobSubmitResult.

    Notes
    -----
    - This function does NOT raise on validation failure.
    - All errors are registered into `result.errors`.
    """
    try:
        check_job(schedule, registry, result)
        check_schedule_for_overlapping_events(schedule, result)
    except Exception as e:
        # Defensive catch: unexpected validator failures should not crash submission
        logger.exception(e)
        return

    # If no errors were registered, validation succeeds
    if len(result.errors) == 0:
        result.validation_success = True


def check_job(schedule: Schedule, registry: EquipmentRegistry, result: JobSubmitResult):
    """
    Validate that each Schedule Resource maps to registered equipment
    and that all Events refer to valid actions.
    """
    for resource in schedule.resources:
        # Resource must correspond to known equipment
        if resource.name not in registry.equipment:
            result.register_error(
                ValueError(f"{resource.name} not in 'registered equipment'.")
            )
            continue

        # Validate each Event assigned to this resource
        for event in resource.events:
            check_event(event, registry.equipment[resource.name], result)


def check_event(event: Event, equipment_interface: EquipmentInterface, result: JobSubmitResult):
    """
    Validate a single Event against its EquipmentInterface.

    Checks
    ------
    - Action exists on the equipment.
    - Event arguments match required inputs.
    - Continuous profiles (if present) are validated at endpoints.
    """
    action = event.callable_

    # Validate action name
    if action not in equipment_interface.action_names:
        result.register_error(
            ValueError(f"{event.resource}.{action} not valid action.")
        )
        return

    # Validate direct event arguments
    validate_event_arguments(
        f"{event.resource}.{action}",
        event.kwargs,
        equipment_interface.get_action(action).inputs,
        result,
    )

    # Special handling for continuous/profiles-based events
    if event.kwargs is not None and "profile" in event.kwargs:
        profile_: ContinuousEventHandler = event.kwargs["profile"]

        # Validate first profile step
        validate_event_arguments(
            profile_.callable_,
            profile_.step_as_dict(0, with_time=False),
            equipment_interface.get_action(profile_.callable_).inputs,
            result,
        )

        # Validate last profile step
        validate_event_arguments(
            profile_.callable_,
            profile_.step_as_dict(-1, with_time=False),
            equipment_interface.get_action(profile_.callable_).inputs,
            result,
        )
        # NOTE: Only the first and last steps are validated currently.


def validate_event_arguments(
    event_label: str,
    kwargs: dict[str, Any],
    inputs: list[ActionParameter],
    result: JobSubmitResult,
):
    """
    Validate event arguments against action input definitions.

    Parameters
    ----------
    event_label : str
        Human-readable label used for error messages.
    kwargs : dict[str, Any]
        Arguments supplied to the event.
    inputs : list[ActionParameter]
        Action input definitions.
    result : JobSubmitResult
        Collector for validation errors.
    """
    # Case: action takes no parameters
    if len(inputs) == 0:
        if kwargs is None:
            return
        raise ValueError("No arguments for this action but some were given.")

    # Track which required parameters are still missing
    required_actions = [arg.required for arg in inputs]
    input_names = [input_.name for input_ in inputs]

    # Validate provided arguments
    if not kwargs:
        if not any(required_actions):
            return

        for k, v in kwargs.items():
            if k not in input_names:
                result.register_error(
                    ValueError(f"{event_label}: '{k}' is invalid parameter.")
                )
                continue

            index = input_names.index(k)
            required_actions[index] = False

            try:
                inputs[index].validate(v)
            except (ValueError, TypeError) as e:
                result.register_error(
                    type(e)(f"{event_label}: " + traceback.format_exc())
                )

    # Detect missing required parameters
    if any(required_actions):
        result.register_error(
            ValueError(
                f"{event_label}: the following are missing required parameters:\n"
                + "\n".join(
                    f"\t{kwarg.name}"
                    for v, kwarg in zip(required_actions, inputs)
                    if v
                )
            )
        )


# ==============================================================================
# Overlap detection
# ==============================================================================

def check_schedule_for_overlapping_events(schedule: Schedule, result: JobSubmitResult):
    """
    Detect overlapping events on the same resource.
    """
    for resource in schedule.resources:
        conflicts = check_resource_for_overlapping_events(resource)
        if conflicts:
            for conflict in conflicts:
                result.register_error(
                    ValueError(
                        f"Overlapping events in {resource.name} schedule.\n "
                        f"Events: "
                        f"{resource.events[conflict[0]].name} - "
                        f"{resource.events[conflict[1]].name} "
                        f"({conflict})"
                    )
                )


def check_resource_for_overlapping_events(resource: Resource, window: int = 2) -> list:
    """
    Check a single Resource timeline for overlapping Events.

    Parameters
    ----------
    resource : Resource
        Resource to examine.
    window : int
        Lookahead window for overlap detection (default: 2).

    Returns
    -------
    list
        List of index pairs indicating overlapping events.
    """
    conflicts = []

    # Assumes events are already time-ordered
    length = len(resource.events)
    for i in range(length):
        for ii in range(i + 1, i + 1 + window):
            if ii > length - 1:
                continue
            if resource.events[i].time_end > resource.events[ii].time_start:
                conflicts.append((i, ii))

    return conflicts
