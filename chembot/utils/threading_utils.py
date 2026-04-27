import sys
import os
import threading
import logging
import time
from typing import Protocol

from chembot.configuration import config

logger = logging.getLogger(config.root_logger_name)


class EquipmentInterface(Protocol):
    """
    Structural protocol describing the minimal interface required
    for equipment managed by EquipmentManager.

    Purpose
    -------
    - Enable static typing without hard coupling to concrete equipment classes.
    - Standardize lifecycle management expectations.

    Required Attributes / Methods
    -----------------------------
    name : str
        Human-readable identifier for the equipment.
    activate()
        Start the equipment main loop (typically blocking).
    write_deactivate()
        Trigger a safe shutdown sequence.
    """
    name = ""

    def activate(self):
        ...

    def write_deactivate(self):
        ...


# Type alias for flexible equipment input
EQUIPMENT_TYPE = (
    list[EquipmentInterface, ...]
    | tuple[EquipmentInterface, ...]
    | EquipmentInterface
)


def equipment_to_list(equipment: EQUIPMENT_TYPE):
    """
    Normalize equipment input into a list.

    Purpose
    -------
    - Allow callers to pass a single equipment or a collection.
    - Simplify downstream processing.
    """
    if not isinstance(equipment, (list, tuple)):
        equipment = [equipment]
    return equipment


class EquipmentManager:
    """
    Lifecycle and threading manager for equipment objects.

    Purpose
    -------
    - Start each equipment in its own thread.
    - Coordinate activation and graceful shutdown.
    - Provide context-manager semantics for automatic cleanup.

    Design Notes
    ------------
    - Equipment.activate() is expected to block.
    - Shutdown is cooperative via write_deactivate().
    """

    def __init__(self, equipment: EQUIPMENT_TYPE = None):
        self.equipment = []
        self.threads = {}

        if equipment is not None:
            self.add(equipment)

    # --------------------------------------------------
    # Context-manager support
    # --------------------------------------------------

    def __enter__(self):
        """
        Enable `with EquipmentManager(...) as mgr:` usage.
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Ensure equipment is deactivated on exit.
        """
        self.deactivate()

    # --------------------------------------------------
    # Equipment management
    # --------------------------------------------------

    def add(self, equipment: EQUIPMENT_TYPE):
        """
        Register one or more equipment instances with the manager.
        """
        self.equipment += equipment_to_list(equipment)

    def activate(self):
        """
        Start all registered equipment in separate threads.
        """
        self.threads = {
            equip.name: threading.Thread(
                target=equip.activate,
                name=equip.name,
            )
            for equip in self.equipment
        }

        # Start all equipment threads with a short stagger
        for thread in self.threads.values():
            thread.start()
            time.sleep(0.2)

        logger.info(
            "UTILS || All threads started\n" + "#" * 48 + "\n\n\n"
        )

    def deactivate(self):
        """
        Gracefully shut down all equipment threads and exit process.

        Behavior
        --------
        - Waits for threads to naturally terminate.
        - Sends deactivation signals if still running.
        - Joins threads with retries.
        - Forces process exit as final guarantee.
        """
        try:
            # Wait until all threads naturally stop
            while True:
                if all(
                    not thread.is_alive()
                    for thread in self.threads.values()
                ):
                    break
                time.sleep(0.2)

        except KeyboardInterrupt:
            logger.info("\n\n\tKeyboardInterrupt raised\n")

        finally:
            logger.info("UTILS || Cleaning up threads")

            # Signal deactivation if equipment is still active
            for equip in self.equipment:
                if not equip._deactivation_event:
                    equip.write_deactivate()
                    logger.debug(
                        f"UTILS || Deactivating thread: {equip.name}"
                    )
                    time.sleep(0.2)

            # Join threads with retries
            for _ in range(3):
                for thread in self.threads.values():
                    if thread.is_alive():
                        thread.join(0.2)

        # Force full process termination
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)


class ThreadWithReturnValue(threading.Thread):
    """
    Thread subclass capable of returning a value from its target.

    Purpose
    -------
    - Allow synchronous-style calls using threads.
    - Retrieve the return value after thread completion.
    """

    def __init__(self, group=None, target=None, name=None, args=(), kwargs=None):
        super().__init__(group, target, name, args, kwargs)
        self._return = None

    def run(self):
        """
        Execute the target and store its return value.
        """
        if self._target is not None:
            self._return = self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        """
        Join the thread and return the target's return value.
        """
        super().join(timeout)
        return self._return


def timeout_wrapper(callable_: callable, timeout: int = None):
    """
    Execute a callable in a thread with an optional timeout.

    Purpose
    -------
    - Run potentially blocking calls safely.
    - Retrieve return value if completed in time.
    """
    thread_ = ThreadWithReturnValue(target=callable_)
    thread_.start()
    return thread_.join(timeout=timeout)