from chembot.rabbitmq.messages import RabbitMessageAction
from chembot.rabbitmq.rabbit_core import RabbitMQConnection
from chembot.master_controller.master_controller import MasterController
from chembot.scheduler.job import Job
from chembot.scheduler.schedular import Schedular
from chembot.equipment.equipment_interface import EquipmentRegistry
from chembot.scheduler.submit_result import JobSubmitResult


class JobSubmitter:
    """
    Client-side helper for submitting and managing jobs via the MasterController.

    Purpose
    -------
    - Provide a simple API for external components (GUI, scripts, services)
      to interact with the MasterController's scheduling functionality.
    - Encapsulate RabbitMQ messaging details for job validation, submission,
      and system queries.
    - Act as the standard entry point for creating and managing jobs.

    Design Notes
    ------------
    - This class does NOT schedule jobs itself.
    - All scheduling logic lives in the MasterController and Schedular.
    - Communication is synchronous (request–reply) via RabbitMQ.
    """

    # Logical RabbitMQ identity for this client
    name = "job_submitter"

    def __init__(self):
        """
        Initialize a RabbitMQ connection for job submission.

        Notes
        -----
        - Creates a dedicated queue using the JobSubmitter's name.
        - Used for receiving replies from the MasterController.
        """
        self.rabbit = RabbitMQConnection(self.name)

    # -------------------------
    # Job lifecycle operations
    # -------------------------

    def validate(self, job: Job) -> JobSubmitResult:
        """
        Validate a job without submitting it.

        Parameters
        ----------
        job : Job
            The job definition to validate.

        Returns
        -------
        JobSubmitResult
            Result indicating whether the job is valid and why/why not.

        Notes
        -----
        - Does not modify scheduler state.
        - Useful for preflight validation in GUIs or APIs.
        """
        message = RabbitMessageAction(
            destination=MasterController.name,
            source=self.name,
            action=MasterController.write_validate_job,
            kwargs={"job": job},
        )
        return (
            self.rabbit
            .send_and_consume(message, timeout=1, error_out=True)
            .value
        )

    def submit(self, job: Job) -> JobSubmitResult:
        """
        Submit a validated job to the scheduler.

        Parameters
        ----------
        job : Job
            Job definition to enqueue.

        Returns
        -------
        JobSubmitResult
            Successful submission result including scheduling metadata.

        Raises
        ------
        ValueError
            If the job submission fails.
        """
        message = RabbitMessageAction(
            destination=MasterController.name,
            source=self.name,
            action=MasterController.write_add_job,
            kwargs={"job": job},
        )
        reply: JobSubmitResult = (
            self.rabbit
            .send_and_consume(message, timeout=1, error_out=True)
            .value
        )

        if not reply.success:
            raise ValueError(str(reply))

        return reply

    def delete(self, job: Job):
        """
        Delete a previously submitted job.

        Notes
        -----
        - Not implemented.
        - Would require scheduler-side support for job removal.
        """
        raise NotImplementedError

    # -------------------------
    # System queries
    # -------------------------

    def get_schedule(self) -> Schedular:
        """
        Retrieve the current job schedule from the MasterController.

        Returns
        -------
        Schedular
            The active scheduler instance (including queued jobs).
        """
        message = RabbitMessageAction(
            destination=MasterController.name,
            source=self.name,
            action=MasterController.read_schedule,
        )
        return (
            self.rabbit
            .send_and_consume(message, timeout=1, error_out=True)
            .value
        )

    def get_equipment_registry(self) -> EquipmentRegistry:
        """
        Retrieve the current equipment registry.

        Returns
        -------
        EquipmentRegistry
            All registered equipment and their interfaces.
        """
        message = RabbitMessageAction(
            destination=MasterController.name,
            source=self.name,
            action=MasterController.read_equipment_registry,
        )
        return (
            self.rabbit
            .send_and_consume(message, timeout=1, error_out=True)
            .value
        )

    def stop(self):
        """
        Stop all scheduled jobs and halt equipment via the MasterController.

        Notes
        -----
        - This is a system-level stop.
        - Clears scheduler queue and signals all equipment to stop.
        - Does not wait for a reply.
        """
        message = RabbitMessageAction(
            destination=MasterController.name,
            source=self.name,
            action=MasterController.write_stop,
        )
        self.rabbit.send(message)