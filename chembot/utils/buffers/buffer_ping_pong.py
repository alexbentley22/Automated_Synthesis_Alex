import pathlib
import queue
import threading
import numpy as np
from typing import Sequence


class SavingThread:
    """
    Background thread responsible for asynchronously saving data to disk.

    Purpose
    -------
    - Decouple data acquisition from disk I/O.
    - Ensure that saving large arrays does not block the main thread.
    - Serialize writes to avoid file name collisions.

    Design Notes
    ------------
    - Uses a bounded Queue (size=1) to provide backpressure.
    - Terminates automatically when the main thread exits and queue is empty.
    """

    def __init__(self, path: pathlib.Path):
        self._path = None
        self.path = path

        # Queue holds data blocks waiting to be saved
        self.data_queue = queue.Queue(maxsize=1)

        # Background worker thread
        self.thread = threading.Thread(target=self._run)
        self.thread.start()

        # Incremented to generate unique filenames
        self.file_counter = 0

    @property
    def path(self) -> pathlib.Path | None:
        """Base output path (CSV extension enforced)."""
        return self._path

    @path.setter
    def path(self, path: str | pathlib.Path):
        # Normalize path and enforce .csv suffix
        if not isinstance(path, pathlib.Path):
            path = pathlib.Path(path)
        if path.suffix != ".csv":
            path = path.with_suffix(".csv")
        self._path = path

    def add_data_to_queue(self, data: np.ndarray):
        """
        Enqueue data to be saved.

        Notes
        -----
        - Blocks if a previous save is still pending.
        - Provides natural throttling for disk I/O.
        """
        self.data_queue.put(data)

    def _get_file_path(self) -> pathlib.Path:
        """
        Generate a unique file path for the next save operation.
        """
        path = self.path.with_stem(
            self.path.stem + "_" + str(self.file_counter)
        )

        counter = 0
        while True:
            if path.exists():
                path = path.with_stem(
                    self.path.stem + "_" + str(counter)
                )
                counter += 1
            else:
                break

        return path

    def _run(self):
        """
        Worker loop for the saving thread.

        Behavior
        --------
        - Waits for data in the queue.
        - Saves data blocks as they become available.
        - Exits once the main thread has terminated and the queue is empty.
        """
        main_thread = threading.main_thread()

        while True:
            try:
                data = self.data_queue.get(timeout=0.25)
                self._save(data)
            except queue.Empty:
                if not main_thread.is_alive() and self.data_queue.empty():
                    break

    def _save(self, data):
        """
        Write a block of data to disk as CSV.
        """
        np.savetxt(self._get_file_path(), data, delimiter=",")
        self.file_counter += 1


class PingPongBuffer:
    """
    High‑throughput buffer using a ping‑pong (double‑buffer) strategy.

    Purpose
    -------
    - Collect large streams of numeric data efficiently.
    - Alternate between two buffers to avoid blocking on disk writes.
    - Enable near‑real‑time data logging for experiments or sensors.

    Design Notes
    ------------
    - One buffer is active (writing), the other passive (saving).
    - When full, buffers are swapped and passive buffer is saved asynchronously.
    """

    def __init__(self, path: pathlib.Path, capacity: int = 50_000):
        self.buffer_active = None
        self.buffer_passive = None

        self.capacity = capacity
        self.position = 0
        self.total_rows = 0

        # Background saving thread
        self.saving_thread = SavingThread(path)

    # -------------------------
    # Context‑manager support
    # -------------------------

    def __enter__(self):
        """Enable usage via `with PingPongBuffer(...) as buffer:`."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Ensure all remaining data is saved on exit."""
        self.save_all()

    # -------------------------
    # Data ingestion
    # -------------------------

    def add_data(
        self,
        data: int | float | Sequence[int | float] | np.ndarray
    ):
        """
        Append a new data row to the buffer.

        Behavior
        --------
        - Lazily creates buffers on first call.
        - Writes data into active buffer.
        - Triggers flush when buffer is full.
        """
        try:
            # Fast path: buffer already initialized
            self.buffer_active[self.position, :] = data
            self.position += 1
            self._check()
        except TypeError:
            # First write: infer data shape and allocate buffers
            self._create_buffer(data)
            self.buffer_active[self.position, :] = data
            self.position += 1

        self.total_rows += 1

    def _check(self):
        """
        Check whether the active buffer is full and needs swapping.
        """
        if self.position == len(self.buffer_active):
            # Swap buffers
            self.buffer_active, self.buffer_passive = (
                self.buffer_passive,
                self.buffer_active,
            )
            self.position = 0
            self.save_passive()

    def _create_buffer(
        self,
        data: int | float | Sequence[int | float] | np.ndarray,
    ):
        """
        Allocate active and passive buffers based on first data sample.
        """
        if isinstance(data, int):
            self.buffer_active = np.zeros(
                (self.capacity, 1),
                dtype=np.int64,
            )
        elif isinstance(data, float):
            self.buffer_active = np.zeros(
                (self.capacity, 1),
                dtype=np.float64,
            )
        elif isinstance(data, (list, tuple)):
            self.buffer_active = np.zeros(
                (self.capacity, len(data))
            )
        elif isinstance(data, np.ndarray):
            self.buffer_active = np.zeros(
                (self.capacity, data.shape[0]),
                dtype=data.dtype,
            )
        else:
            raise TypeError(
                f"Invalid type.\n"
                f"Given: {data}\n"
                f"Expected: int | float | Sequence | np.ndarray"
            )

        self.buffer_passive = np.zeros_like(self.buffer_active)

    # -------------------------
    # Saving operations
    # -------------------------

    def save_all(self):
        """
        Save any remaining data in the active buffer.
        """
        self.saving_thread.add_data_to_queue(
            self.buffer_active[: self.position]
        )
        # Passive buffer should already be flushed

    def save_passive(self):
        """
        Save the passive buffer asynchronously.
        """
        self.saving_thread.add_data_to_queue(self.buffer_passive)


def main():
    """
    Simple example demonstrating PingPongBuffer usage.
    """
    path = pathlib.Path(__file__).parent / "data.csv"

    with PingPongBuffer(path, capacity=105) as buffer:
        for i in range(110):
            buffer.add_data((i, 1.2))

    print("done")


if __name__ == "__main__":
    main()