def sig_figs(number: float | int, sig_digit: int = 3) -> int | float:
    """
    Reduce a numeric value to a specified number of significant figures.

    Purpose
    -------
    - Provide a simple, standardized way to round numbers by significant digits
      rather than fixed decimal places.
    - Preserve the numeric type (int or float) where possible.

    Parameters
    ----------
    number : float | int
        The numeric value to be rounded.
    sig_digit : int
        The number of significant digits to retain.

    Returns
    -------
    float | int
        The rounded value with the requested significant figures.

    Raises
    ------
    TypeError
        If `number` is not an int or float.
    """
    if isinstance(number, float):
        # Use general format to round to significant figures
        return float("{:.{p}g}".format(number, p=sig_digit))

    elif isinstance(number, int):
        # Preserve integer type when possible
        return int("{:.{p}g}".format(number, p=sig_digit))

    else:
        # Explicit type enforcement for safety
        raise TypeError(
            f"'sig_figs' only accepts int or float. "
            f"Given: {number} (type: {type(number)})"
        )