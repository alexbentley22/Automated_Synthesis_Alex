from typing import Protocol

import numpy as np


class Buffer(Protocol):
    """
    Structural typing protocol for circular or appendable buffers.

    Purpose
    -------
    - Define a minimal, shared interface for buffer-like objects.
    - Enable static typing and duck-typing without inheritance.
    - Allow different buffer implementations to be used interchangeably.

    Required Attributes
    -------------------
    dtype : Any
        Underlying NumPy dtype of the buffer.
    shape : tuple[int, int]
        Shape of the underlying data array.
    buffer : np.ndarray
        Storage backing the buffer.
    position : int
        Current write index within the buffer.
    capacity : int
        Maximum number of rows the buffer can hold.
    """

    dtype = None
    shape: tuple[int, int]
    buffer: np.ndarray
    position: int
    capacity: int

    def add_data(self, data: int | float | np.ndarray):
        """
        Append or insert a new data sample into the buffer.
        """
        ...


class BufferSavable(Buffer):
    """
    Extended buffer protocol supporting persistence operations.

    Purpose
    -------
    - Standardize how buffers expose saving and reset behavior.
    - Enable higher-level orchestration code to treat all savable buffers uniformly.
    - Provide an abstract contract for asynchronous or chunked persistence.

    Notes
    -----
    - Inherits all requirements from `Buffer`.
    - Adds saving semantics without enforcing implementation details.
    """

    save_data: bool

    def save(self, last_save: int, next_save: int):
        """
        Persist a range of data from the buffer.

        Parameters
        ----------
        last_save : int
            Starting index (inclusive).
        next_save : int
            Ending index (exclusive).
        """
        ...

    def save_all(self):
        """
        Persist all buffered data.
        """
        ...

    def reset(self):
        """
        Reset the buffer state after saving.
        """
        ...

    def save_and_reset(self):
        """
        Convenience method to persist all data and then reset the buffer.
        """
        self.save_all()
        self.reset()