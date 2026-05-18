import functools

import numpy as np
import plotly.graph_objs as go
from plotly.subplots import make_subplots

from unitpy import U, Quantity, Unit


def get_index(x: np.ndarray, value: float) -> int:
    return int(np.argmin(np.abs(x-value)))

def func(t, x_0: float, delta: float, T: float, rad: float):
    return x_0 * (1 + delta * np.sin((2 * np.pi * t) / T + rad))


def calc_derivative(func, t, h):
    # Calculate the numerical derivative using the central difference method
    f_prime = (func(t + h) - func(t - h)) / (2 * h)
    return f_prime


def find_max_derivative(func, t):
    max_derivative = 0
    t_max_derivative = 0

    h = t[1] - t[0]
    for t_ in t:
        derivative = calc_derivative(func, t_, h)
        if derivative > max_derivative:
            max_derivative = derivative
            t_max_derivative = t_

    print("max_temp change", max_derivative, "degC/min", t_max_derivative)


def main():
    t_total = 444
    n = t_total * 6
    t = np.linspace(0, t_total, n)  # minutes
    res_time = func(t, 7, 0.7143, 55.5, np.pi)
    light = func(t, 0.425, 0.8824, 222, np.pi)
    cat_ratio = func(t, 8.75E-5, 0.7143, 111, 0)
    temp = func(t, 303.15, 0.066, 444, 0) - 273

    print("reactor volume: ", t_total / np.mean(res_time), "min")
    print("volume needed:", t_total / np.mean(res_time) * 0.52, "ml")
    find_max_derivative(functools.partial(func, x_0=303.15, delta=0.066, T=444, rad=0), t)

    # fig = go.Figure()
    # fig.add_trace(go.Scatter(x=t, y=res_time))
    # fig.add_trace(go.Scatter(x=t, y=light))
    # fig.add_trace(go.Scatter(x=t, y=cat_ratio))
    # fig.add_trace(go.Scatter(x=t, y=temp))
    # fig.write_html("temp.html", auto_open=True)

    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, subplot_titles=['res_time', 'light', 'cat_ratio', 'temp'])
    fig.add_trace(go.Scatter(x=t, y=res_time), row=1, col=1)
    fig.add_trace(go.Scatter(x=t, y=light), row=2, col=1)
    fig.add_trace(go.Scatter(x=t, y=cat_ratio), row=3, col=1)
    fig.add_trace(go.Scatter(x=t, y=temp), row=4, col=1)
    fig.layout.showlegend = False
    fig.write_html("temp0.html", auto_open=True)


    reactor_length = 115 * U.cm
    reactor_diameter = 0.762 * U.mm
    reactor_volume = (reactor_length * np.pi * (reactor_diameter / 2) ** 2).to("ml")

    Qtotal = reactor_volume / (res_time * U.min)  # ml/min
    Qfl = Qtotal/2
    Q2 = (cat_ratio * Qtotal / 0.0002).to("mL/min")
    Q1 = ((0.0002-(2*cat_ratio)) * Q2 / 2 /cat_ratio).to("ml/min")

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=Q1.to("ml/min").v, name="Q1"))
    fig.add_trace(go.Scatter(x=t, y=Q2.to("ml/min").v, name="Q2"))
    fig.add_trace(go.Scatter(x=t, y=Qfl.to("ml/min").v, name="Qfl"))
    fig.add_trace(go.Scatter(x=t, y=Qtotal.to("ml/min").v, name="Qtotal"))
    h = np.max(Qtotal)

    refill_indices = get_refill_indices(t, Q1, Q2, Qfl, 5 * Unit.ml, 5 * Unit.ml, 6 * Unit.ml)

    valleys = find_valleys(Q1.v)
    index_ = [valley[2] for valley in valleys]
    np_index = np.array(index_, dtype=np.uint32)
    fig.add_trace(go.Scatter(x=t[np_index], y=Q1.v[np_index], mode="markers"))

    fig.write_html("temp1.html", auto_open=True)

    print()
    print(refill_indices)
    print(t[refill_indices])
    print("index", "Q1", "Q2", "fl")
    for i in range(1, len(refill_indices)):
        print(i,
              np.trapz(x=t[refill_indices[i-1]:refill_indices[i]], y=Q1.v[refill_indices[i-1]:refill_indices[i]]),
              np.trapz(x=t[refill_indices[i-1]:refill_indices[i]], y=Q2.v[refill_indices[i-1]:refill_indices[i]]),
              np.trapz(x=t[refill_indices[i - 1]:refill_indices[i]], y=Qfl.v[refill_indices[i - 1]:refill_indices[i]])
              )

    data = np.column_stack((t, temp, light, Q1.v, Q2.v, Qfl.v, res_time, cat_ratio))
    np.savetxt("AB_replicating_DW2_14_profiles.csv", data, delimiter=",")



def get_refill_indices(t_min, Q1, Q2, Qfl, V1, V2, Vfl):
    """
    Compute refill indices such that no pump exceeds its allowed volume.

    Parameters
    ----------
    t_min : np.ndarray
        Time array in minutes
    Q1, Q2, Qfl : Quantity
        Flow rates (ml/min)
    V1, V2, Vfl : Quantity
        Allowed volumes per segment (ml)

    Returns
    -------
    indices : list[int]
        Indices into t_min at which refills must occur
    """

    t = t_min
    Q1 = Q1.to("ml/min").v
    Q2 = Q2.to("ml/min").v
    Qfl = Qfl.to("ml/min").v

    V1 = V1.to("ml").v
    V2 = V2.to("ml").v
    Vfl = Vfl.to("ml").v

    remain1, remain2, remainfl = V1, V2, Vfl
    indices = [0]

    for i in range(1, len(t)):
        dt = t[i] - t[i - 1]

        remain1  -= dt * (Q1[i] + Q1[i - 1]) / 2
        remain2  -= dt * (Q2[i] + Q2[i - 1]) / 2
        remainfl -= dt * (Qfl[i] + Qfl[i - 1]) / 2

        if remain1 <= 0 or remain2 <= 0 or remainfl <= 0:
            indices.append(i)
            remain1, remain2, remainfl = V1, V2, Vfl

    indices.append(-1)
    return indices


def find_valleys(arr):
    """
    Find the lowest point in each valley within a 1D NumPy array.

    Parameters:
    - arr: NumPy array
        The 1D array containing the function.

    Returns:
    - valley_minima: list of tuples
       (start_index, end_index, lowest_index, lowest_value)
    """
    valley_minima = []
    start_index = 0
    end_index = 0
    descending = False
    lowest_value = None
    lowest_index = None

    for i in range(1, len(arr)):
        if arr[i - 1] < arr[i]:
            # Ascending
            if descending:
                lowest_value = arr[i-1]
                lowest_index = i - 1
                descending = False

        elif arr[i - 1] > arr[i]:
            # Descending
            if not descending:
                # starting the descent
                descending = True
                start_index = i
                if lowest_value is not None:
                    end_index = i-1
                    valley_minima.append((start_index, end_index, lowest_index, lowest_value))
                    lowest_value = None
                    lowest_index = None
    else:
        if lowest_value is not None:
            end_index = len(arr)
            valley_minima.append((start_index, end_index, lowest_index, lowest_value))

    return valley_minima


if __name__ == "__main__":
    main()
