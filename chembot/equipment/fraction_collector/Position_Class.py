# This class acts as an intermediary between the main program and the low-level motor drivers.
# It translates high-level tube labels (e.g., "A1", "B3") into absolute X/Y/Z positions,
# converts position deltas into motor rotations, and calls the underlying stepper/H-bot drivers.
#
# Responsibilities
# ----------------
# - Maintain the current head position (x_now, y_now, z_now) in centimeters.
# - Provide a "layout" function that maps tube labels to absolute coordinates for a given rack.
# - Convert delta distances to motor degrees (xyz_to_deg) for the Z stepper and XY H-bot.
# - Optionally plot tube positions and the head position for visualization.
#
# Dependencies
# ------------
# - Stepper_Motor_Class: a stepper driver for the Z axis (with .zero() and .rotate()).
# - H_Bot_Class: an H-bot driver for XY (with .zero() and .rotate(rot_1, rot_2)).
# - Matplotlib (optional) for plotting/visualization.

from string import ascii_uppercase
import re
import matplotlib.pyplot as plt
import Stepper_Motor_Class
import H_Bot_Class

# Generate a mapping from letters to their 1-based indices: {'A':'1', 'B':'2', ..., 'Z':'26'}
# Used to parse the row letter in a tube label like "A1" → ('A', '1') → (1, 1)
LETTERS = {letter: str(index) for index, letter in enumerate(ascii_uppercase, start=1)}


class FractionCollector:
    """
    High-level fraction collector controller.

    Parameters
    ----------
    layout : str
        The name of the layout method to use (e.g., "layout_23ml_test_tubes"). This class
        expects a method with that name to exist and accept the documented signature.
    x_P1, y_P1, z_P1 : float
        Absolute coordinates (cm) of the first tube position (e.g., "A1") relative to the mechanical origin.
    plot : bool
        If True, initialize plotting (grid of tube positions and current head position).
    motor : bool
        If True, enable motor control (instantiate motor drivers, zero, and go to A1).
        If False, only compute positions; useful for dry-run or plotting without hardware.

    Attributes
    ----------
    x_now, y_now, z_now : float
        Current absolute head position (cm).
    z_motor : Stepper_Motor_Class.StepperMotor
        Z-axis motor driver (created if motor=True).
    xy_motor : H_Bot_Class.HBot
        XY H-bot motor driver (created if motor=True).
    plot_op : bool
        Whether plotting is enabled (used by plot() / plot_update()).
    """

    def __init__(self, layout="layout_23ml_test_tubes", x_P1=4, y_P1=4, z_P1=16, plot=False, motor=True):
        # Home (reference) position for the first tube ("A1")
        self.x_P1 = x_P1   # cm, X of first tube (relative to machine origin)
        self.y_P1 = y_P1   # cm, Y of first tube (relative to machine origin)
        self.z_P1 = z_P1   # cm, Z of first tube (relative to machine origin)

        # Current head position (initialize at first tube for convenience)
        self.x_now = self.x_P1
        self.y_now = self.y_P1
        self.z_now = self.z_P1

        # Layout method name; used to compute absolute coordinates from a tube label
        self.layout = layout

        # Motor enable flag
        self.motor = motor
        if self.motor:
            # Define motors/drivers (pin numbers assume BOARD numbering on the RPi)
            # Z-axis stepper (single-axis driver with end stop)
            self.z_motor = Stepper_Motor_Class.StepperMotor(
                13, 11, 15, step_res='full', end_stop_pin=36, max_rotation=360
            )
            # XY H-bot stepper pair (with X and Y end stops)
            self.xy_motor = H_Bot_Class.HBot(
                37, 35, 40, 31, 29, 33, step_res='full', end_stop_pin_x=32, end_stop_pin_y=38
            )

            # Zero (home) both axes, then move to the first position "A1"
            self.zero()
            self.move("A1")

        # Optional plotting of rack + head position
        self.plot_op = plot
        if self.plot_op:
            # Evaluate the layout to get all tube coordinates (grid=True returns all positions)
            # getattr(self, self.layout) returns the layout method by name.
            self.x_tubes, self.y_tubes, self.z_tubes = getattr(self, self.layout)(
                "A1", self.x_P1, self.y_P1, self.z_P1, grid=True
            )
            self.plot()

    def zero(self):
        """
        Home all axes using end stops, then (implicitly) the head will be moved to A1 via move("A1") in __init__.
        """
        # Z home first (depends on your mechanics; this calls the low-level driver's routine)
        self.z_motor.zero()
        # XY home via H-bot procedure (end stops must be wired to match H_Bot_Class expectations)
        self.xy_motor.zero()

    def move(self, tube):
        """
        Move the head to a tube label (e.g., "A1", "C6") using the configured layout.

        Steps
        -----
        1) Convert the label to target absolute coordinates (x_out, y_out, z_out).
        2) Compute deltas from the current position (dx, dy, dz).
        3) Convert deltas to motor rotations (deg) for (rot_1, rot_2, rot_z).
        4) Rotate Z first (if needed), then XY together via the H-bot driver.
        5) Update current position.
        """
        # NOTE: This calls a specific layout method directly. If you want to support
        # multiple layouts dynamically, consider using getattr(self, self.layout)(...).
        x_out, y_out, z_out = self.layout_23ml_test_tubes(tube, self.x_P1, self.y_P1, self.z_P1)

        # Compute distance deltas (cm)
        dx = self.x_now - x_out
        dy = self.y_now - y_out
        dz = self.z_now - z_out

        # Update current absolute position
        self.x_now = x_out
        self.y_now = y_out
        self.z_now = z_out

        # Convert delta distances to motor rotations (deg)
        rot_1, rot_2, rot_z = self.xyz_to_deg(dx, dy, dz)

        # Execute motion if motors enabled
        if self.motor:
            if rot_z != 0:  # skip Z move if no change
                self.z_motor.rotate(rot_z)
            self.xy_motor.rotate(rot_1, rot_2)

        # Optional debug/plot output (NOTE: in the original code, `self.plot` is referenced,
        # but the flag is `self.plot_op`. If you want this print block to run, consider updating
        # the attribute name. Here we keep original logic unchanged.)
        if self.plot:
            print("Tube: ", position, " pos: ", x_out, y_out, z_out, " cm  rot:", rot_1, rot_2, rot_z, " deg")

    @staticmethod
    def xyz_to_deg(dx, dy, dz):
        """
        Convert delta distances (cm) to motor rotation degrees for the Z stepper and H-bot XY.

        Parameters
        ----------
        dx, dy, dz : float
            Delta distances (cm) from current position to target.

        Returns
        -------
        (rot_1, rot_2, rot_z) : tuple[float, float, float]
            Degrees of rotation for H-bot motor 1, H-bot motor 2, and Z motor.

        Notes
        -----
        - `pitch` is the lead (cm) per full revolution for the Z screw.
        - r_z_motor and r_z_screw form the transmission ratio for Z.
        - r_xy is the H-bot belt pulley radius (cm) used to translate linear distance to degrees.
        - H-bot mapping: rot_1 ∝ (-dx + dy), rot_2 ∝ (-dx - dy) for this specific belt routing.
        """
        # Mechanical constants (must match your actual build)
        pitch = 0.2        # cm/turn (lead of Z screw)
        r_z_motor = 1.5/2  # cm, pulley radius on Z motor shaft
        r_z_screw = 3/2    # cm, pulley radius on Z screw
        r_xy = 1.5/2       # cm, pulley radius used in H-bot drive

        # Z rotation (deg): transmission ratio * linear travel / pitch * 360
        rot_z = (r_z_motor / r_z_screw) * (dz / pitch) * 360.0

        # H-bot rotations (deg): simple linear mapping (depends on your belt routing/geometry)
        rot_1 = (1 / r_xy) * (-dx + dy) * 360.0
        rot_2 = (1 / r_xy) * (-dx - dy) * 360.0

        return rot_1, rot_2, rot_z

    # -----------------
    # Plotting utilities
    # -----------------

    def plot(self):
        """
        Initialize the plot: draw all tube positions and the current head position.
        """
        plt.axis([0, max(self.x_tubes) + self.x_P1, 0, max(self.y_tubes) + self.y_P1])
        plt.ion()
        plt.show()
        plt.plot(self.x_tubes, self.y_tubes, 'ro')                   # tube grid (red dots)
        plt.plot(self.x_now, self.y_now, 'bo', markersize=15)        # head position (blue dot)
        plt.draw()
        plt.pause(0.001)

    def plot_update(self, x, y):
        """
        Update the plot with a new head position and draw a small arrow from old to new.
        """
        plt.cla()
        plt.axis([0, max(self.x_tubes) + self.x_P1, 0, max(self.y_tubes) + self.y_P1])
        plt.plot(self.x_tubes, self.y_tubes, 'ro')
        plt.plot(x, y, 'bo', markersize=15)
        plt.arrow(self.x_now, self.y_now, x - self.x_now, y - self.y_now, width=0.05, length_includes_head=True)
        plt.pause(0.001)

    # -----------------
    # Layout(s)
    # -----------------

    def layout_23ml_test_tubes(self, tube, x_P1, y_P1, z_P1, grid=False):
        """
        Compute coordinates for a 23 mL test-tube rack layout: 15 cols (x) × 12 rows (y → A..L).

        Parameters
        ----------
        tube : str
            Tube label like 'A1', 'B6', 'L15'. Ignored if grid=True.
        x_P1, y_P1, z_P1 : float
            Base position (cm) for the first tube location (A1).
        grid : bool
            If False, return coordinates (x,y,z) for a single tube.
            If True, return arrays of all tube positions (x[], y[], z[]).

        Returns
        -------
        If grid is False:
            (x, y, z) : tuple[float, float, float]
        If grid is True:
            (x[], y[], z[]) : tuple[list[float], list[float], list[float]]

        Notes
        -----
        - The rack has a gap in X after the 7th column; positions ≥ 8 have an extra spacing.
        - All units are in centimeters.
        """
        # Rack geometry
        x_spacing = 2.08  # cm between columns
        y_spacing = 2.1   # cm between rows
        x_spots = 15      # number of columns
        y_spots = 12      # number of rows (A..L)

        if not grid:
            # ---- Single tube position ----

            # Parse tube string into ['A', '1']
            tube_list = re.findall(r'\d+|\D+', tube)
            pos_x = int(tube_list[1])
            pos_y = int(LETTERS[tube_list[0]])

            # Validate indices
            if len(tube_list) > 2:
                raise ValueError('Invalid tube position format. Examples: A1, B3, C6, etc.')
            if pos_x < 0 or pos_x > x_spots:
                raise ValueError('Invalid tube position. Numbers from [1,15] are accepted.')
            if pos_y < 0 or pos_y > y_spots:
                raise ValueError('Invalid tube position. Letters from [A,L] are accepted.')

            # Compute absolute coordinates (A1 is at x_P1, y_P1, z_P1)
            # Apply an extra spacing (gap) after column 7
            if pos_x < 8:
                x = x_P1 + x_spacing * (pos_x - 1)
            else:
                x = x_P1 + x_spacing * pos_x  # to account for gap in tray
            y = y_P1 + y_spacing * (pos_y - 1)
            z = z_P1

            # Optional plotting (NOTE: original code references self.plot, but flag is plot_op)
            if self.plot:
                self.plot_update(x, y)

            return x, y, z

        else:
            # ---- Full grid of tube positions ----
            x = [0] * x_spots * y_spots
            y = [0] * x_spots * y_spots
            z = [0] * x_spots * y_spots

            for i in range(x_spots):      # columns
                for ii in range(y_spots): # rows
                    # Index into flat arrays
                    idx = ii + (i - 1) * y_spots

                    # Compute X with the gap after column 7
                    if i < 8:
                        x[idx] = x_P1 + x_spacing * i
                    else:
                        x[idx] = x_P1 + x_spacing * (i + 1)

                    # Compute Y
                    y[idx] = y_P1 + y_spacing * ii

                    # Z is constant for all tubes in this layout
                    z = z_P1

            return x, y, z


##############################################################################################################
# Run Code (plotting only right now)
##############################################################################################################

if __name__ == '__main__':
    # Example: show the rack and interactively move the head (no motor control)
    FC = FractionCollector(plot=True, motor=False)  # Turn motor=True for motor control
    print("Enter 'end' to stop.")
    try:
        while True:
            position = input("Enter test tube position (format: A1, C13, etc.):")
            if position == "end":
                break
            FC.move(position)
    finally:
        print("End of code!")