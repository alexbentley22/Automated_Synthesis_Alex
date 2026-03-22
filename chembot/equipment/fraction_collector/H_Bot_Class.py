# This module drives two A4988 stepper motor drivers in an H-bot configuration
# using a Kysan 1124090 NEMA 17 stepper motor (or similar).
#
# References
# ----------
# A4988 (Pololu): https://www.pololu.com/product/1182
# Kysan 1124090 NEMA 17: https://ultimachine.com/products/kysan-1124090-nema-17-stepper-motor
#
# Motor (typical) specs (from vendor)
# -----------------------------------
# - Holding Torque:  5.5 kg·cm
# - Phases:          2
# - Step Angle:      1.8° ± 5%
# - Resistance/phase: 2.8 Ω ± 10%
# - Inductance/phase: 4.8 mH ± 20%
# - Current/phase:   1.5 A
# - Shaft:           5 mm (one flat)
#
# Hardware assumptions
# --------------------
# - Raspberry Pi is used to generate STEP/DIR/SLEEP signals for two A4988 drivers.
# - End stops are wired to GPIO inputs and read using a small EndStop wrapper.
# - BOARD numbering is used (physical pins 1..40).
# - Microstepping selection (MS1/MS2/MS3) is handled in hardware wiring.
#
# Safety
# ------
# - Ensure appropriate current limiting on the A4988 to protect the motors.
# - Provide external power and proper power decoupling for motors/drivers.
# - Do not drive coils from Pi GPIO; only control logic lines (STEP/DIR/SLEEP).
# - Provide flyback protection and follow A4988 datasheet recommendations.

from time import sleep, time
import RPi.GPIO as GPIO
from End_Stop_Class import EndStop  # expects EndStop(pin).check_status()

# Use physical header numbering for clarity when wiring to HAT/headers.
GPIO.setmode(GPIO.BOARD)


class HBot:
    """
    H-bot kinematics driver for two A4988-driven stepper motors.

    This class directly pulses STEP pins with fixed delays (software-timed) and
    toggles DIR pins to set direction. It optionally uses two end stops to perform
    a "zero" (homing) procedure along both axes.

    Parameters
    ----------
    pin_step, pin_dir, pin_sleep : int
        BOARD (physical) pins for Motor 1: STEP, DIR, SLEEP
    pin_step2, pin_dir2, pin_sleep2 : int
        BOARD pins for Motor 2: STEP, DIR, SLEEP
    step_res : {'full','half','quarter','eighth','sixteenth'}
        Microstepping resolution (must match hardware MS1..MS3 wiring).
    end_stop_pin_x : int
        BOARD pin for X-axis end stop input.
    end_stop_pin_y : int
        BOARD pin for Y-axis end stop input.
    Acc_control : bool
        Placeholder for acceleration control (currently not implemented).

    Notes
    -----
    - Pulse timing is generated in Python with `sleep()`. Maximum reliable pulse
      rate is limited by OS scheduling; a lower bound (~300 µs) is enforced.
    - `rotate(rot_1, rot_2)` inputs are in degrees at the motor shaft (pre-kinematics).
    - For true H-bot kinematics (mapping X/Y to motor rotations), you would
      typically convert cartesian moves to (rot_1, rot_2) externally.
    """

    def __init__(self, pin_step, pin_dir, pin_sleep, pin_step2, pin_dir2, pin_sleep2, step_res='full',
                 end_stop_pin_x=32, end_stop_pin_y=38, Acc_control=False):
        # -------------------------
        # Motor 1 GPIO configuration
        # -------------------------
        self.pin_step = pin_step
        self.pin_dir = pin_dir
        self.pin_sleep = pin_sleep
        GPIO.setup(self.pin_step, GPIO.OUT)
        GPIO.setup(self.pin_dir, GPIO.OUT)
        GPIO.setup(self.pin_sleep, GPIO.OUT)
        GPIO.output(self.pin_step, 0)
        GPIO.output(self.pin_dir, 0)
        GPIO.output(self.pin_sleep, 0)  # sleep driver by default

        # -------------------------
        # Motor 2 GPIO configuration
        # -------------------------
        self.pin_step2 = pin_step2
        self.pin_dir2 = pin_dir2
        self.pin_sleep2 = pin_sleep2
        GPIO.setup(self.pin_step2, GPIO.OUT)
        GPIO.setup(self.pin_dir2, GPIO.OUT)
        GPIO.setup(self.pin_sleep2, GPIO.OUT)
        GPIO.output(self.pin_step2, 0)
        GPIO.output(self.pin_dir2, 0)
        GPIO.output(self.pin_sleep2, 0)  # sleep driver by default

        # -------------------------
        # Stepping (microstep resolution)
        # -------------------------
        # IMPORTANT: `step_res` must match MS1/MS2/MS3 wiring on the A4988 board.
        self.step_res = step_res
        self.full_step_angle = 1.8  # degrees per full step
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
        # Speed configuration (software-timed)
        # -------------------------
        # `rot_RPM` defines nominal rotation speed. The code turns this into a per-step pulse delay.
        self.rot_RPM = 400  # 350 typical; can be increased if torque allows (up to ~1000 noted)
        # time per *step* at given RPM and microstep angle
        self.step_rate = 1 / (self.rot_RPM / 60 * 360) * self.step_angle
        # pulse spacing (on/off halves); here `delay` is half the full step period
        self.delay = self.step_rate / 2

        # Enforce a practical lower bound on timing due to Python/OS jitter
        if self.delay < 0.0003:  # ~300 µs; Raspberry Pi generally cannot toggle faster reliably in Python
            print(self.delay)
            self.delay = 0.0003
            print('RPM too high, and automatically lowered')

        # Acceleration placeholders (not currently used)
        self.acc_op = Acc_control
        self.rot_RPM_slow = 20  # starting RPM for hypothetical acceleration ramps
        self.step_rate_slow = 1 / (self.rot_RPM_slow / 60 * 360) * self.step_angle
        self.delay_slow = self.step_rate_slow / 2
        self.acceleration_steps = 30

        # -------------------------
        # Current direction state (0=cw, 1=ccw)
        # -------------------------
        self.rot_dir1 = 0
        self.rot_dir2 = 0

        # -------------------------
        # End stops (X, Y)
        # -------------------------
        # EndStop.check_status() returns raw logic (1=HIGH, 0=LOW). Whether that means "pressed"
        # depends on your pull-up/pull-down wiring. This code uses equality checks against 0
        # as "not yet pressed" in the homing loops below.
        self.end_stop_x = EndStop(end_stop_pin_x)
        self.end_stop_y = EndStop(end_stop_pin_y)

    def rotate(self, rot_1, rot_2):
        """
        Rotate both motors by the requested shaft angles (degrees).

        Parameters
        ----------
        rot_1 : float
            Motor 1 rotation in degrees (positive/negative set direction).
        rot_2 : float
            Motor 2 rotation in degrees.

        Behavior
        --------
        - Wakes both drivers (SLEEP=1).
        - Sets DIR pins based on sign of rotations.
        - Pulses STEP pins for each motor (simultaneously while both need steps;
          then finishes the one that has remaining steps).
        - Sleeps drivers again after a short hold time.

        Notes
        -----
        - This method does not implement acceleration ramps (placeholder exists).
        - The final 1 s hold (before sleeping) helps detent against inertia.
        """
        # ---- Wake drivers ----
        GPIO.output(self.pin_sleep, 1)
        GPIO.output(self.pin_sleep2, 1)
        sleep(0.01)  # give drivers time to wake

        # ---- Set directions based on sign of requested rotations ----
        direction1 = 0 if rot_1 < 0 else 1
        direction2 = 0 if rot_2 < 0 else 1

        if direction1 != self.rot_dir1:
            self.rot_dir1 = direction1
            GPIO.output(self.pin_dir, direction1)
            sleep(0.01)

        if direction2 != self.rot_dir2:
            self.rot_dir2 = direction2
            GPIO.output(self.pin_dir2, direction2)
            sleep(0.01)

        # ---- Determine step counts ----
        number_steps1 = round(rot_1 / self.step_angle)
        number_steps2 = round(rot_2 / self.step_angle)

        # ---- Pulse generation (no acceleration control) ----
        if not self.acc_op:
            if number_steps1 > number_steps2:
                # Case 1: Motor 1 has more steps than Motor 2
                number_steps = number_steps1
                for i in range(number_steps):
                    if i < number_steps2:
                        # Both motors step
                        GPIO.output(self.pin_step, 1)
                        GPIO.output(self.pin_step2, 1)
                        sleep(self.delay)
                        GPIO.output(self.pin_step, 0)
                        GPIO.output(self.pin_step2, 0)
                        sleep(self.delay)
                    else:
                        # Only Motor 1 steps
                        GPIO.output(self.pin_step, 1)
                        sleep(self.delay)
                        GPIO.output(self.pin_step, 0)
                        sleep(self.delay)
            else:
                # Case 2: Motor 2 has >= steps compared to Motor 1
                number_steps = number_steps2
                for i in range(number_steps):
                    if i < number_steps1:
                        # Both motors step
                        GPIO.output(self.pin_step, 1)
                        GPIO.output(self.pin_step2, 1)
                        sleep(self.delay)
                        GPIO.output(self.pin_step, 0)
                        GPIO.output(self.pin_step2, 0)
                        sleep(self.delay)
                    else:
                        # Only Motor 2 steps
                        GPIO.output(self.pin_step2, 1)
                        sleep(self.delay)
                        GPIO.output(self.pin_step2, 0)
                        sleep(self.delay)
        else:
            # Placeholder for implementing acceleration/deceleration profiles
            pass

        # ---- Sleep drivers after holding briefly to combat inertia ----
        sleep(1)
        GPIO.output(self.pin_sleep, 0)   # save power and thermal on A4988
        GPIO.output(self.pin_sleep2, 0)

    def zero(self):
        """
        Home (zero) the H-bot against end stops along X then Y.

        Procedure
        ---------
        1) Wake drivers.
        2) Move away from X end stop for a fixed number of steps (ensure not already pressed).
        3) Move toward X end stop until it triggers (or 5 s timeout).
        4) Move toward Y end stop until it triggers (or 5 s timeout).
        5) Sleep drivers.

        Notes
        -----
        - `zero_adj_delay` scales the stepping delay to move more slowly during homing.
        - End stop logic uses `.check_status() == 0` as "not hit yet" — make sure your wiring
          and pull-up/down configuration matches this assumption.
        - Exits the program if end stop is not hit within 5 seconds (safety).
        """
        zero_adj_delay = 1.5  # scale factor to slow movement during homing

        # ---- Wake drivers ----
        GPIO.output(self.pin_sleep, 1)
        GPIO.output(self.pin_sleep2, 1)
        sleep(0.01)

        # ---- Move away from end stop (Motor 1) ----
        # Set DIR for "away" (here, 0). Adjust if your mechanics differ.
        if self.rot_dir1 != 0:
            self.rot_dir1 = 0
            GPIO.output(self.pin_dir, self.rot_dir1)
            sleep(0.01)

        # Step a fixed distance away from potential contact
        for i in range(400):
            GPIO.output(self.pin_step, 1)
            sleep(self.delay * zero_adj_delay * zero_adj_delay)
            GPIO.output(self.pin_step, 0)
            sleep(self.delay * zero_adj_delay * zero_adj_delay)
        sleep(1)

        # ---- Home X end stop (both motors step together toward X stop) ----
        # Set directions toward X end stop (here both set to 0)
        if self.rot_dir1 != 0:
            self.rot_dir1 = 0
            GPIO.output(self.pin_dir, self.rot_dir1)
        if self.rot_dir2 != 0:
            self.rot_dir2 = 0
            GPIO.output(self.pin_dir2, self.rot_dir2)
        sleep(0.01)

        # Step until end_stop_x triggers or timeout (5 s)
        start_time = time()
        while True:
            if time() - start_time > 5:
                exit("Motor did not hit 'x' end stop within 5 sec. Check for issues.")

            if self.end_stop_x.check_status() == 0:
                GPIO.output(self.pin_step, 1)
                GPIO.output(self.pin_step2, 1)
                sleep(self.delay * zero_adj_delay)
                GPIO.output(self.pin_step, 0)
                GPIO.output(self.pin_step2, 0)
                sleep(self.delay * zero_adj_delay)
            else:
                break

        # ---- Home Y end stop ----
        # Set directions toward Y end stop (Motor 1 dir=1, Motor 2 dir=0 in this setup)
        if self.rot_dir1 != 1:
            self.rot_dir1 = 1
            GPIO.output(self.pin_dir, self.rot_dir1)
        if self.rot_dir2 != 0:
            self.rot_dir2 = 0
            GPIO.output(self.pin_dir2, self.rot_dir2)
        sleep(0.01)

        # Step until end_stop_y triggers or timeout (5 s)
        start_time = time()
        while True:
            if time() - start_time > 5:
                exit("Motor did not hit 'x' end stop within 5 sec. Check for issues.")  # typo left as-is (matches original)

            if self.end_stop_y.check_status() == 0:
                GPIO.output(self.pin_step, 1)
                GPIO.output(self.pin_step2, 1)
                sleep(self.delay * zero_adj_delay)
                GPIO.output(self.pin_step, 0)
                GPIO.output(self.pin_step2, 0)
                sleep(self.delay * zero_adj_delay)
            else:
                break

        # ---- Sleep drivers after homing ----
        sleep(1)
        GPIO.output(self.pin_sleep, 0)
        GPIO.output(self.pin_sleep2, 0)

    def cleanup():
        """
        Deprecated local cleanup. Use module-level `cleanup()` instead.
        """
        GPIO.cleanup()


def cleanup():
    """
    Reset all GPIO channels that have been used by this program.
    Call this in a finally-block or on program shutdown.
    """
    GPIO.cleanup()


if __name__ == "__main__":
    # Demo usage:
    # example=1  → simple rotations
    # example=2  → home to end stops (zero)
    example = 2
    try:
        if example == 1:
            # Simple motor turning demo
            xy_axis = HBot(37, 35, 40, 31, 29, 33, step_res='full', end_stop_pin_x=32, end_stop_pin_y=38)
            sleep(.1)
            print('Moving')
            xy_axis.rotate(-360 * 5, 0)
            xy_axis.rotate(-360 * 5, -360 * 5)
            xy_axis.rotate(360 * 5, -360 * 5)
            sleep(1)
            xy_axis.rotate(360 * 5, 0)
            xy_axis.rotate(360 * 5, 360 * 5)
            xy_axis.rotate(+360 * 5, 360 * 5)
            print('moving done')

        if example == 2:
            # Homing test
            xy_axis = HBot(37, 35, 40, 31, 29, 33, step_res='full', end_stop_pin_x=32, end_stop_pin_y=38)
            sleep(.1)
            print('Moving to zero.')
            xy_axis.zero()
            print('Zero reached.')

    finally:
        cleanup()
        print('Program done')