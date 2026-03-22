import time
import warnings


def _clamp(value, limits):
    """
    Clamp a value within (lower, upper) limits.

    Parameters
    ----------
    value : float | None
        Value to clamp. If None, returns None.
    limits : tuple[float | None, float | None]
        (lower, upper) limits. Either may be None to indicate no bound.

    Returns
    -------
    float | None
        Clamped value (or None if input was None).
    """
    lower, upper = limits
    if value is None:
        return None
    elif upper is not None and value > upper:
        return upper
    elif lower is not None and value < lower:
        return lower
    return value


try:
    # Prefer a monotonic clock to ensure time deltas never go backwards
    _current_time = time.monotonic
except AttributeError:
    # Fallback for very old Python versions (< 3.3) where monotonic is unavailable
    _current_time = time.time
    warnings.warn('time.monotonic() not available in python < 3.3, using time.time() as fallback')


class PID(object):
    """
    A simple, self-contained PID (Proportional–Integral–Derivative) controller.

    Features
    --------
    - Supports proportional-on-error (classic) or proportional-on-measurement (to reduce overshoot).
    - Optional `sample_time` throttling (compute at fixed intervals).
    - Output clamping with anti-windup: integral term is clamped to respect output limits.
    - Optional manual vs. auto mode: can hand over from manual with a provided last output.
    - `__call__` interface to compute new outputs.

    Typical usage
    -------------
        pid = PID(Kp=1.2, Ki=0.1, Kd=0.01, setpoint=10.0, output_limits=(0, 100))
        while True:
            measurement = read_sensor()
            control = pid(measurement)   # compute control effort
            apply_output(control)
            time.sleep(0.01)

    Notes
    -----
    - The derivative term is implemented as a derivative of the *measurement*
      (i.e., `d_input`), which is common practice to reduce derivative kick.
    - Integral windup is prevented by clamping the integral term to `output_limits`.
    - If `proportional_on_measurement=True`, the proportional action is applied
      on `-Kp * d_input` (and accumulated in `_proportional`) to reduce overshoot.
    """

    def __init__(self,
                 Kp=1.0, Ki=0.0, Kd=0.0,
                 setpoint=0,
                 sample_time=0.01,
                 output_limits=(None, None),
                 auto_mode=True,
                 proportional_on_measurement=False):
        """
        Parameters
        ----------
        Kp : float
            Proportional gain.
        Ki : float
            Integral gain.
        Kd : float
            Derivative gain.
        setpoint : float
            Target setpoint the controller will try to achieve.
        sample_time : float | None
            Minimum time (seconds) between sequential PID updates. If None,
            compute on every call. If not None, PID returns last output until
            at least `sample_time` has elapsed since last update.
        output_limits : tuple[float | None, float | None]
            (lower, upper) bounds for the output. Either can be None for unbounded.
            Limits also bound the integral term to avoid windup.
        auto_mode : bool
            If True, the controller computes outputs; if False, returns last output.
        proportional_on_measurement : bool
            If True, compute the proportional action on measurement changes (d_input),
            which can reduce overshoot in some systems. If False (default),
            uses traditional proportional on error.
        """
        self.Kp, self.Ki, self.Kd = Kp, Ki, Kd
        self.setpoint = setpoint
        self.sample_time = sample_time

        self._min_output, self._max_output = output_limits
        self._auto_mode = auto_mode
        self.proportional_on_measurement = proportional_on_measurement

        # Initialize internal terms and timing
        self.reset()

    def __call__(self, input_, dt=None):
        """
        Compute a new control output for the given measurement `input_`.

        The controller computes P/I/D terms and returns the combined result,
        subject to clamping by `output_limits`. If `sample_time` is set and the
        last update was too recent, returns the previous output (or None if none
        was computed yet).

        Parameters
        ----------
        input_ : float
            Current process variable (measurement).
        dt : float | None
            Optional precomputed timestep (simulation). If None, uses wall clock
            delta based on a monotonic clock. Must be positive if provided.

        Returns
        -------
        float | None
            The control output (possibly clamped). If sampling throttles an update
            and no prior output exists, returns None.

        Raises
        ------
        ValueError
            If a nonpositive dt is provided.
        """
        if not self.auto_mode:
            # Manual mode: do not compute, just return what we last had
            return self._last_output

        now = _current_time()
        if dt is None:
            # Ensure positive dt (avoid division by zero in derivative term)
            dt = now - self._last_time if now - self._last_time else 1e-16
        elif dt <= 0:
            raise ValueError("dt has nonpositive value {}. Must be positive.".format(dt))

        if self.sample_time is not None and dt < self.sample_time and self._last_output is not None:
            # Only update every `sample_time` seconds: return last output until time has passed
            return self._last_output

        # ---- Compute error terms ----
        error = self.setpoint - input_
        # Change in measurement since last update (derivative on measurement)
        d_input = input_ - (self._last_input if self._last_input is not None else input_)

        # ---- Proportional term ----
        if not self.proportional_on_measurement:
            # Classic proportional-on-error
            self._proportional = self.Kp * error
        else:
            # Proportional on measurement change: accumulate -Kp * d_input
            # This approach can mitigate overshoot by reacting to how measurement changes
            self._proportional -= self.Kp * d_input

        # ---- Integral term ----
        self._integral += self.Ki * error * dt
        # Anti-windup: clamp integral within output limits
        self._integral = _clamp(self._integral, self.output_limits)

        # ---- Derivative term ----
        # Derivative of measurement (negative sign because derivative on measurement,
        # not error; this avoids derivative kick when setpoint steps)
        self._derivative = -self.Kd * d_input / dt

        # ---- Final output ----
        output = self._proportional + self._integral + self._derivative
        output = _clamp(output, self.output_limits)

        # ---- Bookkeeping ----
        self._last_output = output
        self._last_input = input_
        self._last_time = now

        return output

    def update_setpoint(self, new_set_point):
        """
        Update the controller's setpoint.

        Parameters
        ----------
        new_set_point : float
            Desired new setpoint.
        """
        self.setpoint = new_set_point

    @property
    def components(self):
        """
        Return the last computed P, I, and D components as a tuple.

        Useful for debugging, visualization, or tuning.
        """
        return self._proportional, self._integral, self._derivative

    @property
    def tunings(self):
        """
        Current controller tunings as a tuple: (Kp, Ki, Kd).
        """
        return self.Kp, self.Ki, self.Kd

    @tunings.setter
    def tunings(self, tunings):
        """
        Set the controller tunings.

        Parameters
        ----------
        tunings : tuple[float, float, float]
            (Kp, Ki, Kd)
        """
        self.Kp, self.Ki, self.Kd = tunings

    @property
    def auto_mode(self):
        """
        Whether the controller is currently in auto mode (enabled).
        """
        return self._auto_mode

    @auto_mode.setter
    def auto_mode(self, enabled):
        """
        Enable or disable auto mode. See also `set_auto_mode`.

        Parameters
        ----------
        enabled : bool
            True to compute outputs; False to hold last output.
        """
        self.set_auto_mode(enabled)

    def set_auto_mode(self, enabled, last_output=None):
        """
        Enable or disable the PID controller, optionally seeding the integral term.

        This is useful when switching from manual control to PID control, so that
        the transition is smooth. If you provide `last_output`, it is used to seed
        the integral term (and then clamped), reducing bumps on takeover.

        Parameters
        ----------
        enabled : bool
            Whether auto mode should be enabled.
        last_output : float | None
            The last output (control variable) to seed the I-term when switching
            from manual to auto. If None, seeds with 0.
        """
        if enabled and not self._auto_mode:
            # Switching from manual → auto: reset and seed integral
            self.reset()

            self._integral = (last_output if last_output is not None else 0)
            self._integral = _clamp(self._integral, self.output_limits)

        self._auto_mode = enabled

    @property
    def output_limits(self):
        """
        Current output limits as a 2-tuple: (lower, upper).
        """
        return self._min_output, self._max_output

    @output_limits.setter
    def output_limits(self, limits):
        """
        Set output limits (and re-clamp integrator/last output accordingly).

        Parameters
        ----------
        limits : tuple[float | None, float | None] | None
            (lower, upper) limits. Either can be None; if None entirely, clears limits.

        Raises
        ------
        ValueError
            If both limits are provided and lower > upper.
        """
        if limits is None:
            self._min_output, self._max_output = None, None
            return

        min_output, max_output = limits

        if None not in limits and max_output < min_output:
            raise ValueError('lower limit must be less than upper limit')

        self._min_output = min_output
        self._max_output = max_output

        # Re-clamp internal state to respect new limits
        self._integral = _clamp(self._integral, self.output_limits)
        self._last_output = _clamp(self._last_output, self.output_limits)

    def reset(self):
        """
        Reset the controller internals:

        - P, I, D components → 0
        - last update time   → now
        - last output/input  → None

        Use this when retuning or after large setpoint changes to clear history.
        """
        self._proportional = 0
        self._integral = 0
        self._derivative = 0

        self._last_time = _current_time()
        self._last_output = None
        self._last_input = None