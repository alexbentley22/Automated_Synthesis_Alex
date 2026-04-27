def get_actions_list(class_) -> list[str]:
    """
    Extract a list of exposed action method names from a class or instance.

    Purpose
    -------
    - Discover callable methods that should be exposed as actions.
    - Standardize how read/write operations are identified across the system.
    - Support dynamic action registration (e.g., for messaging or RPC).

    Behavior
    --------
    - Iterates over all attributes returned by `dir(class_)`.
    - Selects methods whose names start with either:
        * "read_"
        * "write_"
    - Ensures the attribute is callable.
    - Returns a list of method names as strings.

    Parameters
    ----------
    class_ : Any
        A class or instance whose methods should be inspected.

    Returns
    -------
    list[str]
        Names of callable read/write action methods.
    """
    actions = []

    for func in dir(class_):
        # Select public read/write methods only
        if (
            (func.startswith("read_") or func.startswith("write_"))
            and callable(getattr(class_, func))
        ):
            actions.append(func)

    return actions