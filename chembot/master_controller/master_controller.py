import logging
import time
from datetime import datetime, timedelta

from chembot.configuration import config
from chembot.equipment.equipment import Equipment
from chembot.utils.class_building import get_actions_list
from chembot.rabbitmq.messages import (
    RabbitMessage,
    RabbitMessageAction,
    RabbitMessageCritical,
    RabbitMessageError,
    RabbitMessageRegister,
    RabbitMessageReply,
    RabbitMessageUnRegister,
)
from chembot.rabbitmq.rabbit_core import RabbitMQConnection
from chembot.rabbitmq.watchdog import RabbitWatchdog
from chembot.equipment.equipment_interface import EquipmentRegistry
from chembot.scheduler.schedule import Schedule
from chembot.scheduler.schedular import Schedular
from chembot.scheduler.submit_result import JobSubmitResult
from chembot.scheduler.validate import validate_schedule
from chembot.scheduler.job import Job

logger = logging.getLogger(config.root_logger_name + ".master_controller")


class MasterController:
    """
    Central orchestrator for the Chembot system.

    Purpose
    -------
    - Acts as the main event loop and message router for the entire system.
    - Maintains a registry of all connected equipment.
    - Dispatches actions to equipment via RabbitMQ.
    - Monitors communication health via watchdogs.
    - Schedules and executes jobs using the scheduler.
    - Handles critical errors and performs safe system shutdown.

    In short: this is the *brain* of Chembot.
    """

    # Logical name used for RabbitMQ addressing
    name = "master_controller"

    # Main loop timing (seconds)
    pulse = 0.01

    # Periodic equipment status update interval
    status_update_time = timedelta(seconds=1)

    def __init__(self):
        """
        Initialize the master controller and all core subsystems.
        """
        # Introspect and collect callable actions on this controller
        self.actions = get_actions_list(self)

        # RabbitMQ connection for messaging
        self.rabbit = RabbitMQConnection(self.name)

        # Watchdog to monitor outstanding requests
        self.watchdog = RabbitWatchdog(self)

        # Registry of all active equipment
        self.registry = EquipmentRegistry()

        # Job scheduler
        self.scheduler = Schedular()

        # Loop control flag
        self._deactivate_event = True

        # Next time to perform a status update
        self._next_update = datetime.now()

    # -------------------------
    # Lifecycle
    # -------------------------

    def _deactivate(self):
        """
        Safely deactivate the system.

        Behavior
        --------
        - Sends a deactivate command to all equipment (in reverse order).
        - Allows serial-based devices time to shut down cleanly.
        - Shuts down RabbitMQ connection.
        """
        for equip in reversed(self.registry.equipment):
            # Reverse order helps serial devices send stop signals before shutdown
            self.rabbit.send(
                RabbitMessageAction(
                    equip,
                    self.name,
                    Equipment.write_deactivate,
                ),
                check=False,
            )
            time.sleep(0.1)

        self.rabbit.deactivate()
        logger.info(config.log_formatter(self, self.name, "Deactivated"))
        self._deactivate_event = False

    def activate(self):
        """
        Start the master controller main loop.

        Notes
        -----
        - This method blocks until shutdown.
        - All exceptions are caught to ensure safe deactivation.
        """
        logger.info(
            config.log_formatter(
                self, self.name, "Activated\n" + "#" * 80 + "\n\n"
            )
        )
        error_ = None
        try:
            self._run()
        except Exception as e:
            logger.critical(str(e))
            error_ = e
        except KeyboardInterrupt:
            logger.info(config.log_formatter(self, self.name, "KeyboardInterrupt"))
        finally:
            self._deactivate()

        if error_ is not None:
            raise error_

    # -------------------------
    # Main loop
    # -------------------------

    def _run(self):
        """
        Main event loop.

        Executes continuously until `_deactivate_event` is cleared.
        """
        while self._deactivate_event:
            self.watchdog.check_watchdogs()
            self._read_message()
            self._run_event()
            self._status_update()

    def _run_event(self):
        """
        Execute the next scheduled job event, if any.
        """
        event = self.scheduler.get_event_to_run()
        if event is None:
            return

        self.rabbit.send(
            RabbitMessageAction(
                destination=event.resource,
                source=self.name,
                action=event.callable_,
                kwargs=event.kwargs,
                id_job=event.id_job,
            )
        )
        # TODO: handle missing queue / resource failures more robustly

    # -------------------------
    # Message handling
    # -------------------------

    def _read_message(self):
        """
        Consume a message from RabbitMQ with timeout defined by `pulse`.
        """
        message = self.rabbit.consume(self.pulse)
        if message:
            self._process_message(message)

    def _process_message(self, message: RabbitMessage):
        """
        Dispatch incoming RabbitMQ messages based on type.
        """
        if isinstance(message, RabbitMessageCritical):
            self._error_handling()
            self._deactivate_event = False

        elif isinstance(message, RabbitMessageError):
            self._error_handling()
            self._deactivate_event = False

        elif isinstance(message, RabbitMessageRegister):
            # Equipment registering itself
            self.registry.register(message.source, message.equipment_interface)
            self.rabbit.send(RabbitMessageReply.create_reply(message, None))

        elif isinstance(message, RabbitMessageUnRegister):
            self.registry.unregister(message.source)

        elif isinstance(message, RabbitMessageAction):
            self._execute_action(message)

        elif isinstance(message, RabbitMessageReply):
            # Reply to a previously issued command
            self.watchdog.deactivate_watchdog(message)

        else:
            logger.error("Invalid message!!" + message.to_str())
            self.rabbit.send(
                RabbitMessageError(
                    self.name,
                    f"InvalidMessage: {message.to_str()}",
                )
            )

    def _execute_action(self, message):
        """
        Execute an action on the master controller itself.

        Used for:
        - read_equipment_registry
        - scheduling requests
        - job submission and control
        """
        if message.action not in self.actions:
            logger.error("Invalid action!!" + message.to_str())
            self.rabbit.send(
                RabbitMessageError(
                    self.name,
                    f"Invalid action: {message.to_str()}",
                )
            )
            return

        try:
            func = getattr(self, message.action)

            # Call method with or without kwargs
            if func.__code__.co_argcount == 1:
                reply = func()
            else:
                reply = func(**message.kwargs)

            # Send reply if applicable
            if reply is not None:
                self.rabbit.send(
                    RabbitMessageReply.create_reply(message, reply)
                )

            logger.info(
                config.log_formatter(
                    self,
                    self.name,
                    f"Action | {message.action}: {message.kwargs}",
                )
            )

        except Exception:
            logger.error(
                config.log_formatter(
                    self, self.name, "ActionError" + message.to_str()
                )
            )
            logger.exception(config.error())
            logger.info(
                "master controller continues to operate as nothing happened."
            )

    # -------------------------
    # Error handling
    # -------------------------

    def _error_handling(self):
        """
        Handle critical system errors by shutting down all equipment.
        """
        self._deactivate()

    # -------------------------
    # Status updates
    # -------------------------

    def _status_update(self):
        """
        Periodic equipment status update hook.

        Currently disabled (commented out), but intended to:
        - request status from all equipment
        - track responsiveness via watchdogs
        """
        if datetime.now() > self._next_update:
            return

        self._next_update = datetime.now() + self.status_update_time

        # for equipment in self.registry.equipment:
        #     message = RabbitMessageUpdate(equipment.class_name)
        #     self.rabbit.send(message)
        #     self.watchdog.set_watchdog(message, delay=1)

    # -------------------------
    # Public API (exposed actions)
    # -------------------------

    def read_equipment_registry(self) -> EquipmentRegistry:
        """Return the current equipment registry."""
        return self.registry

    def read_schedule(self) -> Schedular:
        """Return the current job scheduler."""
        return self.scheduler

    def write_add_job(self, job: Job) -> JobSubmitResult:
        """
        Validate and enqueue a job.
        """
        result = self.write_validate_job(job)
        if not result.validation_success:
            return result

        self.scheduler.add_job(job)
        result.time_start = self.scheduler.jobs_in_queue[-1].time_start
        result.position_in_queue = len(self.scheduler.jobs_in_queue)
        result.length_of_queue = len(self.scheduler.jobs_in_queue)
        result.success = True

        return result

    def write_validate_job(self, job: Job) -> JobSubmitResult:
        """
        Validate a job against current equipment availability and schedule.
        """
        result = JobSubmitResult(job.id_)
        job.time_start = datetime.now()
        schedule = Schedule.from_job(job)

        validate_schedule(schedule, self.registry, result)
        return result

    def write_stop(self):
        """
        Stop all scheduled jobs and halt all equipment.
        """
        self.scheduler.clear_all_jobs()
        for equip in self.registry.equipment:
            self.rabbit.send(
                RabbitMessageAction(
                    equip,
                    self.name,
                    Equipment.write_stop,
                )
            )

    def write_forward_reply(self, message: RabbitMessageReply, destination: str):
        """
        Forward a reply message to a different destination.
        """
        message.destination = destination
        self.rabbit.send(message)

    def write_deactivate(self):
        """
        External deactivate command hook.
        """
        pass