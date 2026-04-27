import numpy as np


class Filter:
    """
    Base class for data filters.

    Purpose
    -------
    - Provide a common interface for filter implementations.
    - Act as a placeholder/extension point for concrete filters
      (e.g., FIR, IIR, CUSUM-based, exponential smoothing).

    Notes
    -----
    - Currently defines no behavior.
    - Intended to be subclassed.
    """

    def __init__(self):
        pass

    def process_data(self, data):
        """
        Process incoming data and return filtered output.

        Notes
        -----
        - Not implemented in the base class.
        - Subclasses should override this method.
        """
        pass


def savitzky_golay_filter_matrix(
    data: np.ndarray,
    window_size,
    order,
    axis=0,
    deriv=0,
    rate=1,
):
    """
    Apply the Savitzky-Golay filter to a multi-dimensional array.

    Purpose
    -------
    - Perform smoothing or differentiation on data arranged as a matrix.
    - Apply the filter independently along a selected axis.

    Parameters
    ----------
    data : np.ndarray
        Input matrix of data points.
    window_size : int
        Size of the smoothing window (must be positive and odd).
    order : int
        Polynomial order used in smoothing.
    axis : int
        Axis along which to apply the filter.
    deriv : int
        Order of derivative to compute (0 = smoothing).
    rate : int
        Sampling rate of the input data.

    Returns
    -------
    np.ndarray
        Filtered data matrix.
    """
    try:
        window_size = np.abs(int(window_size))
        order = np.abs(int(order))
    except ValueError:
        raise ValueError("window_size and order have to be of type int")

    # Validate window and polynomial order
    if window_size % 2 != 1 or window_size < 1:
        raise TypeError("window_size size must be a positive odd number")
    if window_size < order + 2:
        raise TypeError("window_size is too small for the polynomials order")

    # Apply the 1D Savitzky-Golay filter along the specified axis
    filtered_matrix = np.apply_along_axis(
        lambda x: savitzky_golay_filter(
            x, window_size, order, deriv=deriv, rate=rate
        ),
        axis=axis,
        arr=data,
    )

    return filtered_matrix


def savitzky_golay_filter(
    y,
    window_size,
    order,
    deriv=0,
    rate=1,
):
    """
    Apply the Savitzky-Golay filter to a one-dimensional signal.

    Purpose
    -------
    - Smooth noisy signals while preserving local shape.
    - Compute smoothed derivatives of the signal.

    Parameters
    ----------
    y : np.ndarray
        One-dimensional input signal.
    window_size : int
        Size of the smoothing window (must be positive and odd).
    order : int
        Polynomial order used in the fit.
    deriv : int
        Derivative order (0 = smoothing).
    rate : int
        Sampling rate.

    Returns
    -------
    np.ndarray
        Filtered signal.

    Notes
    -----
    - Uses a least-squares polynomial fit over a sliding window.
    - Pads signal at both ends to avoid boundary effects.
    """
    order_range = range(order + 1)
    half_window = (window_size - 1) // 2

    # Construct Vandermonde matrix
    b = np.mat(
        [[k ** i for i in order_range]
         for k in range(-half_window, half_window + 1)]
    )

    # Compute filter coefficients
    m = (
        np.linalg.pinv(b).A[deriv]
        * rate ** deriv
        * np.factorial(deriv)
    )

    # Pad the signal at the boundaries
    firstvals = y[0] - np.abs(y[1 : half_window + 1][::-1] - y[0])
    lastvals = y[-1] + np.abs(y[-half_window - 1 : -1][::-1] - y[-1])
    y = np.concatenate((firstvals, y, lastvals))

    # Convolve coefficients with padded signal
    return np.convolve(m[::-1], y, mode="valid")


def filter_exponential(
    prior_data: np.ndarray,
    new_data: np.ndarray,
    a: int | float = 0.8,
) -> np.ndarray:
    """
    Apply an exponential (IIR) smoothing filter.

    Purpose
    -------
    - Smooth data by combining previous output with new input.
    - Favor historical data to reduce noise.

    Parameters
    ----------
    prior_data : np.ndarray
        Filtered output from the previous step.
    new_data : np.ndarray
        New input data.
    a : float
        Smoothing coefficient (0 < a < 1).
        Higher values give stronger smoothing.

    Returns
    -------
    np.ndarray
        Filtered output for the current step.
    """
    return a * prior_data + (1 - a) * new_data


# Minimum number of data points required for filter_exponential
filter_exponential.min_number_points = 2


def filter_low_pass_fft(
    data: np.ndarray,
    cutoff_freq: int = 50,
    sampling_freq: int = 1000,
):
    """
    Apply a low-pass filter using the Fast Fourier Transform (FFT).

    Purpose
    -------
    - Remove high-frequency noise from a signal.
    - Preserve signal components below a cutoff frequency.

    Parameters
    ----------
    data : np.ndarray
        Input signal.
    cutoff_freq : int
        Cutoff frequency (Hz).
    sampling_freq : int
        Sampling frequency of the signal (Hz).

    Returns
    -------
    np.ndarray
        Low-pass filtered signal.

    Notes
    -----
    - Filtering is performed in the frequency domain.
    - Assumes uniformly sampled data.
    """
    # Normalize cutoff frequency
    normalized_cutoff_freq = cutoff_freq / (0.5 * sampling_freq)

    # Forward FFT
    fft = np.fft.fft(data)

    # Frequency axis
    freq = np.fft.fftfreq(len(data))

    # Zero out frequencies above cutoff
    fft_filtered = np.where(
        np.abs(freq) <= normalized_cutoff_freq,
        fft,
        0,
    )

    # Inverse FFT back to time domain
    filtered_signal = np.fft.ifft(fft_filtered)

    # Discard imaginary residue
    return np.real(filtered_signal)