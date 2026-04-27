from unitpy import Quantity


def validate_quantity(
    quantity: Quantity,
    dimensionality,
    error_label: str,
    positive: bool = False,
):
    """
    Validate that a unit-aware quantity has the expected dimensionality
    and (optionally) a non-negative value.

    Purpose
    -------
    - Enforce unit correctness using `unitpy.Quantity`.
    - Prevent silent unit mismatches (e.g., passing time where length is expected).
    - Optionally enforce physical constraints such as positivity.

    Parameters
    ----------
    quantity : Quantity
        The value to validate.
    dimensionality : Any
        Expected unit dimensionality (typically from `unitpy`).
    error_label : str
        Context label used to produce clear, user-facing error messages.
    positive : bool, optional
        If True, require the quantity value to be >= 0.

    Raises
    ------
    ValueError
        If dimensionality does not match or positivity is violated.
    """
    # Validate unit dimensionality
    if quantity.dimensionality != dimensionality:
        raise ValueError(
            f"{error_label}: Invalid unit dimensionality.\n"
            f"Given: {quantity.dimensionality}\n"
            f"Expected: {dimensionality}"
        )

    # Optional positivity check
    if positive:
        if quantity.v < 0:
            raise ValueError(
                f"{error_label}: Can't be less than 0.\n"
            )