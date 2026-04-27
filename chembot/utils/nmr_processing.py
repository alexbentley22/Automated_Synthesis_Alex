import time
import pathlib

import numpy as np


def find_most_recent_folder(directory_path, max_repeats=4) -> pathlib.Path:
    """
    Locate the most recently modified subdirectory within a given directory.

    Purpose
    -------
    - Poll a parent directory for newly created subfolders.
    - Return the most recent subfolder if it was created very recently.
    - Useful for workflows where external processes asynchronously
      generate output directories (e.g., instrument data acquisition).

    Parameters
    ----------
    directory_path : str | PathLike
        Path containing candidate subdirectories.
    max_repeats : int
        Number of retry attempts before giving up.

    Returns
    -------
    pathlib.Path
        Path to the most recently created subdirectory.

    Raises
    ------
    RuntimeError
        If no sufficiently recent folder is found within the retry budget.
    """
    directory = pathlib.Path(directory_path)

    for _ in range(max_repeats):
        # Enumerate all immediate subdirectories
        subdirectories = [
            d for d in directory.iterdir()
            if d.is_dir()
        ]

        # Sort by last modification time (newest first)
        sorted_subdirectories = sorted(
            subdirectories,
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )

        if sorted_subdirectories:
            most_recent_folder = sorted_subdirectories[0]

            # Check recency (within last 3 seconds)
            if time.time() - most_recent_folder.stat().st_mtime <= 3:
                return most_recent_folder
            else:
                # Folder exists but is too old; retry
                pass
        else:
            # No subdirectories found; retry
            pass

        # Wait before next polling attempt
        time.sleep(1)

    raise RuntimeError(
        f"Exceeded maximum repeats ({max_repeats}). No recent folder found."
    )


def nmr_check(folder_path: str) -> bool:
    """
    Perform a simple quality check on NMR output data.

    Purpose
    -------
    - Identify the most recent experiment folder.
    - Load processed spectrum data from CSV.
    - Apply a threshold-based heuristic to decide data validity.

    Parameters
    ----------
    folder_path : str
        Path to directory containing NMR experiment subfolders.

    Returns
    -------
    bool
        True  -> data passes quality check
        False -> data fails quality check
    """
    # Find the most recently generated experiment folder
    folder = find_most_recent_folder(folder_path)

    # Expected output file from NMR processing pipeline
    file_path = folder / "spectrum_processed.csv"

    # Load spectral data (assumes: column 1 = signal intensity)
    data = np.loadtxt(
        file_path,
        delimiter=",",
        skiprows=1,
    )

    # Simple heuristic: check for sufficiently strong signal
    if np.max(data[:, 1]) > 40:
        return True

    return False