import numpy as np
from typing import Sized


def get_max_index(index: int | slice | tuple[int]) -> int:
    """
    Compute the maximum absolute index referenced by an indexing operation.

    Purpose
    -------
    - Support bounds checking for DynamicArray indexing.
    - Normalize different indexing types (int, slice, tuple).

    Parameters
    ----------
    index : int | slice | tuple[int]
        Index or slice expression used to access the array.

    Returns
    -------
    int
        Maximum absolute index referenced.
    """
    if isinstance(index, int):
        return abs(index)

    if isinstance(index, tuple):
        return abs(int(index[0]))

    # index must be a slice
    return abs(int(index.stop))


def get_object_size(x: int | float | Sized) -> int:
    """
    Determine how many rows an object contributes when appended.

    Purpose
    -------
    - Normalize scalar vs sequence inputs for DynamicArray.append().
    - Ensure consistent capacity growth calculations.
    """
    if isinstance(x, (int, float)):
        return 1

    if isinstance(x, np.ndarray):
        return x.shape[0]

    if hasattr(x, "__len__"):
        return len(x)

    raise ValueError("Invalid item to add.")


class DynamicArray:
    """
    A dynamically growing NumPy-backed array optimized for appends.

    Purpose
    -------
    - Efficiently append rows to a NumPy array without reallocating
      on every append.
    - Mimic list-like growth behavior while preserving NumPy semantics.

    Supported Use Cases
    -------------------
    - Time series accumulation.
    - Streaming numeric data.
    - Growing column vectors or 2D arrays with fixed column count.

    Design Notes
    ------------
    - Capacity is managed manually to amortize reallocation cost.
    - Only grows along axis 0 (rows); number of columns is fixed.
    """

    def __init__(
        self,
        shape: tuple[int, int] | int = (100,),
        index_expansion: bool = False
    ):
        """
        Parameters
        ----------
        shape : tuple[int, int] | int
            Initial capacity (rows) and optional column count.
        index_expansion : bool
            Whether assignment outside the current size is allowed.
            (Currently unused due to commented-out __setitem__.)
        """
        self._data = None
        self.capacity = shape[0]
        self.size = 0
        self.index_expansion = index_expansion

    def __str__(self):
        """String representation showing active data only."""
        return self.data.__str__()

    def __repr__(self):
        """Verbose representation including size and capacity."""
        return self.data.__repr__().replace(
            "array",
            f"DynamicArray(size={self.size}, capacity={self.capacity})",
        )

    def __getitem__(self, index: int | slice | tuple[int]):
        """
        Retrieve data with bounds checking against current size.
        """
        size = get_max_index(index)
        if size > self.size:
            raise IndexError(
                f"index {size} is out of bounds for axis 0 with size {self.size}"
            )

        return self.data[index]

    def __getattribute__(self, name):
        """
        Forward attribute access to underlying NumPy array when needed.

        Behavior
        --------
        - Attempts normal attribute lookup first.
        - If attribute is missing, dispatches to `self.data`.
        - Callable attributes are wrapped to preserve behavior.
        """
        try:
            attr = object.__getattribute__(self, name)
        except AttributeError:
            # Fallback to NumPy array attributes
            attr = object.__getattribute__(self.data, name)

        if callable(attr):
            def newfunc(*args, **kwargs):
                return attr(*args, **kwargs)
            return newfunc

        return attr

    def __len__(self):
        """Return number of valid rows in the array."""
        return self.size

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def data(self):
        """
        Return active portion of the underlying array.

        Notes
        -----
        - Excludes unused capacity.
        """
        return self._data[:self.size]

    @property
    def shape(self) -> tuple[int, int]:
        """
        Shape of the active data.
        """
        return self.size, self.data.shape[0]

    @property
    def dtype(self):
        """Data type of the underlying NumPy array."""
        return self.data.dtype

    # ------------------------------------------------------------------
    # Mutation methods
    # ------------------------------------------------------------------

    def append(self, x: np.ndarray | list | tuple | int | float):
        """
        Append data to the array, expanding capacity if needed.

        Parameters
        ----------
        x : np.ndarray | list | tuple | int | float
            Data to append along axis 0.
        """
        size = get_object_size(x)
        self._capacity_check(size)

        # Add new data
        self._data[self.size : self.size + size] = x
        self.size += size
        self.capacity -= size

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _capacity_check_index(self, index: int = 0):
        """
        Ensure capacity exists for direct index assignment.

        Notes
        -----
        - Intended for use with index expansion behavior.
        """
        if index > len(self._data):
            add_size = (index - len(self._data)) + self.capacity
            self._grow_capacity(add_size)

    def _capacity_check(self, size: int):
        """
        Ensure underlying array can accommodate `size` new rows.

        Strategy
        --------
        - If remaining capacity is sufficient, do nothing.
        - Otherwise, grow array by doubling or minimal expansion.
        """
        if size < self.capacity:
            return

        # Calculate additional space required
        change_need = size - self.capacity

        shape_ = list(self._data.shape)

        if shape_[0] + self.capacity > size:
            # Double the array size
            self.capacity += shape_[0]
            shape_[0] *= 2
        else:
            # Grow just enough to fit incoming data
            self.capacity += change_need
            shape_[0] += change_need

        newdata = np.zeros(shape_, dtype=self._data.dtype)

        # Copy old data into new array
        newdata[: self._data.shape[0]] = self._data
        self._data = newdata