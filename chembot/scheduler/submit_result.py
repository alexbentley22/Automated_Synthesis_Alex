from datetime import datetime


class JobSubmitResult:
    """
    Result container for job validation and submission operations.

    Purpose
    -------
    - Encapsulate the outcome of validating or submitting a Job.
    - Provide structured feedback to callers (GUI, API, scripts).
    - Capture both success metadata and failure diagnostics.

    Design Notes
    ------------
    - This class contains NO scheduling logic.
    - It is purely a data-transfer and reporting object.
    - Instances are typically created by the MasterController.
    """

    def __init__(self, job_id: int):
        """
        Parameters
        ----------
        job_id : int
            Identifier of the job being validated or submitted.
        """
        self.job_id = job_id

        # Overall submission success flag
        self.success: bool = False

        # Indicates whether validation passed (independent of submission)
        self.validation_success: bool = False

        # Scheduled start time assigned by the scheduler
        self.time_start: datetime | None = None

        # Position of the job in the queue at submission time
        self.position_in_queue: int | None = None

        # Total number of jobs in the queue
        self.length_of_queue: int | None = None

        # Collection of validation or submission errors
        self.errors: list[Exception] = []

    def __str__(self):
        """
        Human-readable summary of the submission result.

        Behavior
        --------
        - On success, shows scheduling metadata.
        - On failure, lists validation status and error count.
        """
        if self.success:
            return (
                f"Success || validation successful: {self.validation_success}, "
                f"start: {self.time_start} ({self.position_in_queue}/{self.length_of_queue})"
            )

        return (
            f"Unsuccessful || validation successful: {self.validation_success}, "
            f"# errors: {len(self.errors)}"
            + "\n\t\t"
            + "\n\t\t".join(repr(e) for e in self.errors)
        )

    def __repr__(self):
        return self.__str__()

    def register_error(self, error: Exception):
        """
        Register an error affecting job submission or validation.

        Parameters
        ----------
        error : Exception
            The exception representing a validation or submission failure.

        Notes
        -----
        - Automatically marks submission as unsuccessful.
        - Does not raise; accumulates errors for reporting.
        """
        self.success = False
        self.errors.append(error)