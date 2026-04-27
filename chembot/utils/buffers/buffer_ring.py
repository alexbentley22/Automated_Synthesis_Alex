import abc
import os
import pathlib
import queue
import threading
import time
import logging

import numpy as np

logger = logging.getLogger("buffer")


class BufferRing:
    """
    Fixed-size circular (ring) buffer for efficient streaming data ingestion.

    Purpose
    -------
    - Store the most recent N samples using constant memory.
    - Overwrite old data automatically without reallocations.
    - Support high-throughput, real-time data streams.

    Design Notes
    ------------
    - Data is written sequentially and wrapped when capacity is reached.
    - Intended for numerical time-series data.
    """

    _DEFAULT_LENGTH = 1000

    def __init__(self, buffer: np.ndarray = None, length: int = None):
        self.buffer = buffer
        self.position = -1
        self.length = length
        self.total_rows = 0

    def __str__(self):
        return f"buffer: {self.shape}, position: {self.position}"

    def __repr__(self):
        return self.__str__()

    @property
    def shape(self) -> tuple[int, int] | None:
        """Return buffer shape, or None if uninitialized."""
        if self.buffer is None:
            return None
        return self.buffer.shape

    @property
    def dtype(self):
        """Return data type of the buffer."""
        if self.buffer is None:
            return None
        return self.buffer.dtype

    @property
    def last_measurement(self) -> np.ndarray | None:
        """Return the most recently written row."""
        if self.buffer is None:
            return None
        return self.buffer[self.position, :]

    def add_data(self, data: int | float | np.ndarray):
        """
        Add a new data point to the ring buffer.
        """
        try:
            # Fast path: buffer already initialized
            self._update_position()
            self.buffer[self.position, :] = data
        except AttributeError:
            if self.buffer is None:
                # Lazy allocation on first write
                self._create_buffer(data)
                self._update_position()
                self.buffer[self.position, :] = data
            else:
                raise

        self.total_rows += 1

    def _update_position(self):
        """Advance write index with wrap-around."""
        if self.position == self.buffer.shape[0] - 1:
            self.position = 0
        else:
            self.position += 1

    def _create_buffer(self, data: int | float | np.ndarray):
        """
        Allocate the underlying NumPy buffer based on the first data item.
        """
        length = self.length if self.length is not None else self._DEFAULT_LENGTH

        if isinstance(data, int):
            self.buffer = np.zeros((length, 1), dtype=np.int64)
        elif isinstance(data, float):
            self.buffer = np.zeros((length, 1), dtype=np.float64)
        elif isinstance(data, np.ndarray):
            self.buffer = np.zeros((length, data.shape[0]), dtype=data.dtype)
        else:
            raise TypeError(
                f"Invalid type.\nGiven: {data}\nExpected: int | float | np.ndarray"
            )

    def _get_data_index(self, start: int = None, end: int = None):
        """
        Compute slice indices accounting for wrap-around.
        """
        if self.buffer is None or self.total_rows == 0:
            return None

        if start is None and end is None:
            if self.total_rows < self.buffer.shape[0]:
                start = 0
                end = self.position
            else:
                start = self.position + 1
                end = self.position + 1

        return start, end

    def get_data(self, start: int = None, end: int = None) -> np.ndarray | None:
        """
        Retrieve buffered data, handling wrap-around automatically.
        """
        start, end = self._get_data_index(start, end)

        if start >= end:
            return np.concatenate(
                (self.buffer[start:, :], self.buffer[0:end, :])
            )

        return self.buffer[start:end, :]

    def reset(self):
        """Reset buffer indices without reallocating memory."""
        self.position = -1
        self.total_rows = 0


class BufferRingTime(BufferRing):
    """
    Ring buffer that tracks timestamps for each data sample.

    Purpose
    -------
    - Associate each data row with its acquisition time.
    - Support time-aligned retrieval and logging.
    """

    def __init__(
        self,
        buffer: np.ndarray = None,
        buffer_time: np.ndarray = None,
        length: int = None,
    ):
        super().__init__(buffer, length)

        if buffer is not None:
            self.buffer_time = np.zeros(self.buffer.shape[0], dtype=np.float64)

        self.buffer_time = buffer_time

    @property
    def last_time(self) -> float:
        """Timestamp of the most recent sample."""
        return self.buffer_time[self.position]

    def add_data(self, data: int | float | np.ndarray):
        """Add data and record timestamp."""
        super().add_data(data)
        self.buffer_time[self.position] = time.time()

    def _create_buffer(self, data: int | float | np.ndarray):
        super()._create_buffer(data)
        self.buffer_time = np.zeros(self.buffer.shape[0], dtype=np.float64)

    def get_data(
        self,
        start: int = None,
        end: int = None,
        merge: bool = True,
    ) -> tuple[np.ndarray, np.ndarray] | np.ndarray | None:
        """
        Retrieve data and timestamps, optionally merged into one array.
        """
        start, end = self._get_data_index(start, end)

        if start >= end:
            data = (
                np.concatenate((self.buffer_time[start:], self.buffer_time[:end])),
                np.concatenate((self.buffer[start:, :], self.buffer[0:end, :])),
            )
        else:
            data = (
                self.buffer_time[start:end],
                self.buffer[start:end, :],
            )

        if merge:
            data = np.column_stack(data)

        return data


class SavingMixin(abc.ABC):
    """
    Mixin providing background CSV saving capability to buffers.

    Purpose
    -------
    - Periodically persist buffer contents to disk.
    - Avoid blocking the main data collection thread.
    - Support long-running experiments with bounded memory.

    Design Notes
    ------------
    - Uses a worker thread and a bounded queue.
    - Splits output across multiple files to limit file size.
    """

    _FILE_SIZE_LIMIT = 30_000

    def __init__(
        self,
        path: str | pathlib.Path,
        queue_size: int,
        saving: bool = True,
    ):
        self._path = None
        self.path = path

        self.saving = saving
        self._data_queue = queue.Queue(maxsize=queue_size)

        self._file_counter = 0
        self._resets = 0
        self._reset_saving = False

        self._thread = threading.Thread(target=self._thread_save)
        self._thread.start()

    @property
    def path(self) -> pathlib.Path | None:
        return self._path

    @path.setter
    def path(self, path: str | pathlib.Path):
        if not isinstance(path, pathlib.Path):
            path = pathlib.Path(path)
        if path.suffix != ".csv":
            path = path.with_suffix(".csv")
        self._path = path

    def get_file_path(self, index: int) -> pathlib.Path:
        """
        Generate a unique file path for each saved chunk.
        """
        path = self.path

        if self._resets > 0:
            path = path.with_stem(f"{path.stem}_reset{self._resets}")

        path = path.with_stem(f"{path.stem}_{index}_{time.time()}")  # TODO: stabilize naming
        return path

    def _thread_save(self):
        """
        Background thread loop responsible for writing buffer data to disk.
        """
        main_thread = threading.main_thread()
        index = -1

        close_thread = False
        while not close_thread:
            index += 1
            counter = 0
            file_path = self.get_file_path(index)

            try:
                logger.warning(f"opening: {file_path}")
                counter = self._write_data_to_file(file_path, counter, main_thread)
                logger.warning(f"closing: {file_path}")
            except PermissionError:
                new_path = file_path.with_stem(f"{file_path.stem}_{int(time.time())}")
                logger.warning(f"{file_path} locked. Renaming to {new_path}")
                counter = self._write_data_to_file(new_path, counter, main_thread)

            if counter == 0:
                logger.warning(f"removing: {file_path}")
                try:
                    os.remove(file_path)
                except PermissionError:
                    logger.warning(
                        "Could not delete empty file; possible concurrent writers."
                    )

    def _write_data_to_file(self, file_path, counter, main_thread):
        with open(file_path, mode="w", encoding="utf-8") as f:
            while True:
                try:
                    range_ = self._data_queue.get(timeout=0.2)
                except queue.Empty:
                    if not main_thread.is_alive():
                        self.save_all()
                        return counter
                    if self._reset_saving:
                        self._reset_saving = False
                        break
                    continue

                logger.warning(f"saving data: {range_}")
                data = self.get_data(*range_)
                if data is None:
                    continue

                for row in data:
                    f.write(",".join(str(i) for i in row) + "\n")

                counter += data.shape[0]
                if counter > self._FILE_SIZE_LIMIT:
                    break

        return counter

    @abc.abstractmethod
    def save_all(self):
        ...

    @abc.abstractmethod
    def get_data(
        self,
        start: int = None,
        end: int = None,
    ) -> np.ndarray | None:
        ...


class BufferRingSavable(BufferRing, SavingMixin):
    """
    Ring buffer with automatic background persistence.

    Purpose
    -------
    - Combine fast in-memory buffering with periodic disk writes.
    - Enable safe, long-running data capture without memory growth.
    """

    _NUMBER_OF_ROWS_PER_SAVE_DEFAULT = 1000
    _SAVING_TIMEOUT = 3  # seconds

    def __init__(
        self,
        path: pathlib.Path,
        buffer: np.ndarray = None,
        number_of_rows_per_save: int = None,
        length: int = None,
    ):
        BufferRing.__init__(self, buffer, length)
        SavingMixin.__init__(self, path, 2)

        self.number_of_rows_per_save = number_of_rows_per_save
        self._last_save = 0
        self._next_save = None

    def add_data(self, data: int | float | np.ndarray):
        BufferRing.add_data(self, data)

        if self.saving and self.position == self._next_save:
            self.save(self._last_save, self.position + 1)

        self.total_rows += 1

    def save_all(self):
        self.save(self._last_save, self.position + 1)
        self.reset()

    def save(self, last_save: int, position: int):
        if last_save == position or position == -1:
            return
        if self._data_queue.full():
            raise OverflowError(
                "Saving thread too slow; increase buffer size or slow acquisition."
            )
        self._data_queue.put((last_save, position))
        self._last_save = position
        self._next_save = self._compute_next_save()

    def _get_number_of_rows_per_save(self):
        if self.number_of_rows_per_save is None:
            self.number_of_rows_per_save = min(
                self._NUMBER_OF_ROWS_PER_SAVE_DEFAULT,
                int(self.buffer.shape[0] / 2) - 1,
            )

    def _create_buffer(self, data: int | float | np.ndarray):
        BufferRing._create_buffer(self, data)
        self._get_number_of_rows_per_save()
        self._next_save = self._compute_next_save()

    def reset(self):
        time_out = time.time() + self._SAVING_TIMEOUT
        while time.time() < time_out:
            if self._data_queue.empty():
                time.sleep(0.1)
                self._reset_saving = True
                BufferRing.reset(self)
                self._resets += 1
                self._last_save = 0
                self._next_save = self._compute_next_save()
                return
            time.sleep(0.1)

        raise TimeoutError("Queue never drained; reset aborted.")

    def _compute_next_save(self):
        rows_left = self.shape[0] - self._last_save
        if rows_left < self.number_of_rows_per_save:
            return self.number_of_rows_per_save - 1 - rows_left
        return self._last_save - 1 + self.number_of_rows_per_save


class BufferRingTimeSavable(BufferRingSavable):
    """
    Time-aware savable ring buffer.

    Purpose
    -------
    - Extend BufferRingSavable with per-sample timestamps.
    """

    def __init__(
        self,
        path: pathlib.Path,
        buffer: np.ndarray = None,
        number_of_rows_per_save: int = None,
        buffer_time: np.ndarray = None,
        length: int = None,
    ):
        super().__init__(path, buffer, number_of_rows_per_save, length=length)

        if buffer is not None:
            self.buffer_time = np.zeros(self.buffer.shape[0], dtype=np.float64)

        self.buffer_time = buffer_time

    @property
    def last_time(self) -> float:
        return self.buffer_time[self.position]

    def add_data(self, data: int | float | np.ndarray):
        BufferRing.add_data(self, data)
        self.buffer_time[self.position] = time.time()

        if self.saving and self.position == self._next_save:
            self.save(self._last_save, self._next_save + 1)

        self.total_rows += 1

    def _create_buffer(self, data: int | float | np.ndarray):
        BufferRingSavable._create_buffer(self, data)
        self.buffer_time = np.zeros(self.buffer.shape[0], dtype=np.float64)

    def get_data(
        self,
        start: int = None,
        end: int = None,
        merge: bool = True,
    ) -> tuple[np.ndarray, np.ndarray] | np.ndarray | None:
        start, end = self._get_data_index(start, end)

        if start >= end:
            data = (
                np.concatenate((self.buffer_time[start:], self.buffer_time[:end])),
                np.concatenate((self.buffer[start:, :], self.buffer[0:end, :])),
            )
        else:
            data = (
                self.buffer_time[start:end],
                self.buffer[start:end, :],
            )

        if merge:
            data = np.column_stack(data)

        return data