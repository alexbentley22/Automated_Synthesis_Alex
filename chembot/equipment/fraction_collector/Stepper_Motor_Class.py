# This module drives a single A4988 stepper motor driver using a Kysan 1124090 NEMA 17 stepper motor (or similar).
#
# References
# ----------
# A4988 (Pololu): https://www.pololu.com/product/1182
# Kysan 1124090 NEMA 17: https://ultimachine.com/products/kysan-1124090-nema-17-stepper-motor
#
# Motor (typical) specs (from vendor)
# -----------------------------------
# - Holding Torque:     5.5 kg·cm
# - Phases:             2
# - Full step angle:    1.8° ± 5%
# - Resistance/phase:   2.8 Ω ± 10%
# - Inductance/phase:   4.8 mH ± 20%
# - Current/phase:      1.5 A
# - Shaft:              5 mm (one flat)
#
# Safety & Wiring Notes
# ---------------------
# - Use appropriate current limiting on A4988 (set via onboard potentiometer).
# - Drive only logic pins (STEP/DIR/SLEEP) from the Raspberry Pi. Motor power must be provided separately.
# - Ensure proper motor power decoupling and heat sinking on the driver.
# - BOARD numbering is used below (physical header pins 1..40).

from time import sleep, time
import RPi.GPIO as GPIO
from End_Stop_Class import EndStop

# Use physical board numbering to match header labels
GPIO.setmode(GPIO.BOARD)


class StepperMotor:
    """
    Single-axis stepper motor control via A4988 driver using Raspberry Pi GPIO.

    Capabilities
    ------------
    - Configure GPIO pins for STEP, DIR, and SLEEP.
    - Set microstepping resolution (must match A4988 MS1/MS2/MS3 hardware jumpers).
    - Rotate by a requested number of degrees, with optional acceleration ramp.
    - Home (zero) against an end stop with timeout and backoff (simple approach).

    Parameters
    ----------
    pin_step : int
        BOARD pin connected to the A4988 STEP input.
    pin_dir : int
        BOARD pin connected to the A4988 DIR input.
    pin_sleep : int
        BOARD pin connected to the A4988 SLEEP input (1=awake, 0=sleep).
    step_res : {'full','half','quarter','eighth','sixteenth'}
        Microstepping setting. MUST match MS1/MS2/MS3 wiring on the A4988 board.
    end_stop_pin : int
        BOARD pin for the end stop input (0 disables end stop usage).
    max_rotation : float
        Max absolute rotation window (degrees) allowed for safety (0..max_rotation).
    Acc_control : bool
        If True, enable a simple acceleration/deceleration ramp for moves.

    Attributes
    ----------
    step_angle : float
        Degrees per microstep, derived from the full step angle (1.8°) and step_res.
    delay : float
        Half-period between STEP toggles at the selected nominal RPM.
    total_rotation : float
        Accumulated angle (deg) since start (used for simple software limits).
    rot_dir : int
        Last direction set on DIR pin (0=clockwise, 1=counterclockwise).
    end_stop : EndStop | None
        If end_stop_pin != 0, an EndStop instance is created and used in zero().
    """

    def __init__(self, pin_step, pin_dir, pin_sleep, step_res='full', end_stop_pin=0, max_rotation=360,
                 Acc_control=False):
        # -------------------------
        # GPIO configuration
        # -------------------------
        self.pin_step = pin_step
        self.pin_dir = pin_dir
        self.pin_sleep = pin_sleep

        GPIO.setup(self.pin_step, GPIO.OUT)
        GPIO.setup(self.pin_dir, GPIO.OUT)
        GPIO.setup(self.pin_sleep, GPIO.OUT)

        # Initialize outputs low (driver asleep)
        GPIO.output(self.pin_step, 0)
        GPIO.output(self.pin_dir, 0)
        GPIO.output(self.pin_sleep, 0)

        # -------------------------
        # Microstepping configuration
        # -------------------------
        # IMPORTANT: `step_res` must match A4988 MS1/MS2/MS3 hardware jumper positions.
        self.step_res = step_res
        self.full_step_angle = 1.8  # degrees per full step (motor spec)

        if self.step_res == 'full':
            self.step_angle = self.full_step_angle
        elif self.step_res == 'half':
            self.step_angle = self.full_step_angle / 2
        elif self.step_res == 'quarter':
            self.step_angle = self.full_step_angle / 4
        elif self.step_res == 'eighth':
            self.step_angle = self.full_step_angle / 8
        elif self.step_res == 'sixteenth':
            self.step_angle = self.full_step_angle / 16
        else:
            exit('invalid stepper resolution')

        # -------------------------
        # Speed / timing setup (software-timed pulses)
        # -------------------------
        # Nominal motor speed (RPM). Python + Linux jitter limits maximum usable pulse rate.
        self.rot_RPM = 400  # 350 typical; up to ~1000 if torque allows
        # For reference: 350 RPM ~ 1000 Hz step rate (0.001 s pulses) at full steps
        #                1000 RPM ~ 3000 Hz step rate (0.00033 s pulses) at full steps

        # step_rate: time per *step* for the microstep angle at the selected RPM
        self.step_rate = 1 / (self.rot_RPM / 60 * 360) * self.step_angle
        # delay: half-period between step rising/falling edges (sleep toggles)
        self.delay = self.step_rate / 2

        # Enforce a practical lower bound due to OS scheduling and Python accuracy
        if self.delay < 0.0003:  # Raspberry Pi toggling below ~300 µs is unreliable in Python
            print(self.delay)
            self.delay = 0.0003
            print('RPM too high, and automatically lowered')

        # -------------------------
        # Acceleration ramp parameters (optional)
        # -------------------------
        self.acc_op = Acc_control
        self.rot_RPM_slow = 20  # entry speed RPM for ramps
        self.step_rate_slow = 1 / (self.rot_RPM_slow / 60 * 360) * self.step_angle
        self.delay_slow = self.step_rate_slow / 2
        self.acceleration_steps = 30  # number of steps to ramp in/out for long moves

        # -------------------------
        # Position / limit state
        # -------------------------
        self.total_rotation = 0
        self.rot_dir = 0           # 0 = CW, 1 = CCW (matches DIR pin)
        self.max_rotation = max_rotation  # allowed window [0, max_rotation] deg (software limit)

        # -------------------------
        # Optional end stop support
        # -------------------------
        if end_stop_pin != 0:
            self.end_stop_pin = end_stop_pin
            self.end_stop = EndStop(end_stop_pin)  # EndStop.check_status() used in zero()

    def rotate(self, degree):
        """
        Rotate the motor by a requested angle (degrees) at nominal speed (with optional ramp).

        Parameters
        ----------
        degree : float
            Positive for one direction (DIR=1), negative for the other (DIR=0).

        Behavior
        --------
        - Wakes the driver (SLEEP=1).
        - Sets direction if needed.
        - Checks software movement window [0..max_rotation] via `total_rotation`.
        - Issues STEP pulses with either:
            * constant delay (no acceleration), or
            * simple accel/decel ramp based on `acceleration_steps`.
        - Sleeps the driver after a short hold (to resist inertia then save power).
        """
        # ---- Wake driver ----
        GPIO.output(self.pin_sleep, 1)
        sleep(0.01)  # give time to wake

        # ---- Set direction based on sign ----
        direction = 0 if degree < 0 else 1
        if direction != self.rot_dir:
            self.rot_dir = direction
            GPIO.output(self.pin_dir, direction)
            sleep(0.01)

        # ---- Software limit check ----
        self.total_rotation = self.total_rotation + degree
        if self.total_rotation > self.max_rotation or self.total_rotation < 0:
            exit('Motor is being asked to move outside the window of [0, max_rotations].')

        # ---- Number of microsteps to perform ----
        number_steps = round(abs(degree) / self.step_angle)

        # ---- Pulse generation ----
        if not self.acc_op:
            # Constant-speed stepping
            for i in range(number_steps):
                GPIO.output(self.pin_step, 1)
                sleep(self.delay)
                GPIO.output(self.pin_step, 0)
                sleep(self.delay)

        else:
            # Acceleration control
            # Strategy:
            #  - If move is long enough: ramp up (acceleration_steps), run full speed, ramp down
            #  - Else: triangular ramp (accelerate then decelerate without full-speed plateau)
            if number_steps > self.acceleration_steps * 2:
                # Long move → trapezoidal profile
                full_speed_steps = number_steps - self.acceleration_steps * 2

                # Acceleration
                for i in range(self.acceleration_steps):
                    delay = self.delay_slow - (self.delay_slow - self.delay) / self.acceleration_steps * i
                    GPIO.output(self.pin_step, 1)
                    sleep(delay)
                    GPIO.output(self.pin_step, 0)
                    sleep(delay)

                # Constant speed plateau
                for i in range(full_speed_steps):
                    GPIO.output(self.pin_step, 1)
                    sleep(self.delay)
                    GPIO.output(self.pin_step, 0)
                    sleep(self.delay)

                # Deceleration
                for i in range(self.acceleration_steps):
                    delay = (self.delay_slow - self.delay) / self.acceleration_steps * i + self.delay
                    GPIO.output(self.pin_step, 1)
                    sleep(delay)
                    GPIO.output(self.pin_step, 0)
                    sleep(delay)

            else:
                # Short move → triangular profile (no plateau)
                tri1_steps = round(number_steps / 2)
                tri2_steps = number_steps - tri1_steps

                # Acceleration (coarse proportional scaling by step index)
                for i in range(tri1_steps):
                    delay = self.delay_slow - (self.delay_slow - self.delay) / (5 * 360 / self.step_angle) * i
                    GPIO.output(self.pin_step, 1)
                    sleep(delay)
                    GPIO.output(self.pin_step, 0)
                    sleep(delay)

                # Deceleration (mirror)
                for i in range(tri2_steps):
                    delay2 = delay + (self.delay_slow - self.delay) / (5 * 360 / self.step_angle) * i
                    GPIO.output(self.pin_step, 1)
                    sleep(delay2)
                    GPIO.output(self.pin_step, 0)
                    sleep(delay2)

        # ---- Sleep driver after brief hold ----
        sleep(1)  # hold to counter inertia briefly (optional; may adjust for thermals)
        GPIO.output(self.pin_sleep, 0)  # save power/thermal on driver

    def zero(self):
        """
        Home (zero) the axis by moving off the end stop and then approaching until it triggers.

        Behavior
        --------
        - Validates an end stop pin was provided at init.
        - Wakes the driver.
        - Moves away from the end stop (fixed steps) to ensure it is not already pressed.
        - Reverses direction and steps until the end stop triggers or a 5 s timeout.
        - Sleeps the driver after a brief hold.

        Notes
        -----
        - EndStop.check_status() is assumed to return 0 while NOT pressed, 1 when pressed
          (or vice versa depending on wiring). This code uses `== 0` as "not yet hit".
          Adjust if your wiring/logic differs.
        - Timeout protection is included to avoid indefinite motion on failures.
        """
        zero_adj_delay = 1.5  # scaling factor to slow homing moves

        # ---- Ensure end stop is available ----
        var = self.end_stop_pin
        check = "var" in locals()
        if not check:
            exit('Endstop pin not given, so zeroing not possibele.')

        # ---- Wake driver ----
        GPIO.output(self.pin_sleep, 1)
        sleep(0.01)

        # ---- Move away from end stop ----
        if self.rot_dir != 0:
            self.rot_dir = 0
            GPIO.output(self.pin_dir, self.rot_dir)
            sleep(0.01)

        for i in range(400):
            GPIO.output(self.pin_step, 1)
            sleep(self.delay * zero_adj_delay)
            GPIO.output(self.pin_step, 0)
            sleep(self.delay * zero_adj_delay)
        sleep(1)

        # ---- Move toward end stop until triggered or timeout ----
        self.rot_dir = 1
        GPIO.output(self.pin_dir, self.rot_dir)
        sleep(0.01)

        start_time = time()
        while True:
            if time() - start_time > 5:
                exit("Motor did not hit end stop within 5 sec. Check for issues.")

            if self.end_stop.check_status() == 0:  # "not hit" (adjust if your wiring is active-low/high)
                GPIO.output(self.pin_step, 1)
                sleep(self.delay * zero_adj_delay)
                GPIO.output(self.pin_step, 0)
                sleep(self.delay * zero_adj_delay)
            else:
                break

        # ---- Sleep driver after homing ----
        sleep(1)
        GPIO.output(self.pin_sleep, 0)


def cleanup():
    """
    Reset all GPIO channels used by this program.
    Call from a finally-block to leave pins in a safe state.
    """
    GPIO.cleanup()


if __name__ == "__main__":
    # Demo usage:
    # example=1 → manual rotations
    # example=2 → homing to end stop
    example = 2
    try:
        if example == 1:
            # Simple motor turning
            z_axis = StepperMotor(35, 37, 33, step_res='full', max_rotation=360 * 100, Acc_control=True)
            sleep(.1)
            print('spin forward')
            z_axis.rotate(360 * 10)     # rotate +10 rev
            sleep(.1)
            print('spin backwards')
            z_axis.rotate(-360 * 10)    # rotate -10 rev

            sleep(.1)
            print('spin forward')
            z_axis.rotate(360 / 4)      # rotate +90°
            sleep(.1)
            print('spin backwards')
            z_axis.rotate(-360 / 4)     # rotate -90°

        if example == 2:
            # Zero test (requires an end stop)
            z_axis = StepperMotor(35, 37, 33, step_res='full', max_rotation=360 * 100, end_stop_pin=31, Acc_control=True)
            sleep(.1)
            print('Moving to zero.')
            z_axis.zero()
            print('Zero reached.')

    finally:
        cleanup()
        print('Program done')