import numpy as np
from qpsolvers import solve_qp

from force_push import util


class RobotController:
    """Controller for the mobile base.

    Designed to achieve commanded EE linear velocity while using base rotation
    to avoid obstacles and otherwise align the robot with the path.
    """

    def __init__(self, r_cb_b, lb, ub, solver="proxqp"):
        """Initialize the controller.

        Parameters:
            r_cb_b: 2D position of the contact point w.r.t. the base frame origin
            lb: Lower bound on velocity (v_x, v_y, ω)
            ub: Upper bound on velocity
            solver: the QP solver to use (default: 'proxqp')
        """
        self.r_cb_b = r_cb_b
        self.lb = lb
        self.ub = ub
        self.solver = solver

    def update(self, C_wb, V_ee_d, normals=None, dists=None):
        """Compute new controller input u.

        Parameters:
            C_wb: rotation matrix from base frame to world frame
            V_ee_d: desired EE velocity (v_x, v_y, ω)
            normals: Normals to nearby obstacles (optional)
            dists: Distances to nearby obstacles (optional)
        """
        S = np.array([[0, -1], [1, 0]])
        J = np.hstack((np.eye(2), (S @ C_wb @ self.r_cb_b)[:, None]))

        P = np.diag([0.1, 0.1, 1])
        q = np.array([0, 0, -V_ee_d[2]])

        lb = np.array([-1, -1, -0.5])
        ub = np.array([1, 1, 0.5])

        if normals is None:
            G = None
            h = None
        else:
            h = dists
            G = np.hstack((normals, np.zeros((normals.shape[0], 1))))

        A = J
        b = V_ee_d[:2]

        u = solve_qp(P=P, q=q, A=A, b=b, G=G, h=h, lb=lb, ub=ub, solver=self.solver)
        return u


class Controller:
    """Task-space force angle-based pushing controller."""

    def __init__(
        self,
        speed,
        kθ,
        ky,
        path,
        ki_θ=0,
        ki_y=0,
        corridor_radius=np.inf,
        force_min=1,
        force_max=50,
        con_inc=0.3,
        div_inc=0.3,
    ):
        """Force-based pushing controller.

        Parameters:
            speed: linear pushing speed
            kθ: gain for stable pushing
            ky: gain for path tracking
            path: path to track
            ki_θ: integral gain for stable pushing
            ki_y: integral gain for path tracking
            force_min: contact requires a force of at least this much
            force_max: diverge if force exceeds this much
            con_inc: increment to push angle to converge back to previous point
            div_inc: increment to push angle to diverge when force is too high
        """
        self.speed = speed
        self.kθ = kθ
        self.ky = ky
        self.path = path

        self.ki_θ = ki_θ
        self.ki_y = ki_y

        # distance from center of corridor the edges
        # if infinite, then the corridor is just open space
        # to be used with hallways (won't work if there aren't actually walls
        # present)
        self.corridor_radius = corridor_radius

        # force thresholds
        # self.force_max = 10
        self.force_min = force_min
        self.force_max = force_max

        # convergence and divergence increment
        self.con_inc = con_inc
        self.div_inc = div_inc

        # variables
        self.first_contact = False
        self.yc_int = 0
        self.θd_int = 0
        self.θp = 0
        self.inc_sign = 1

    def reset(self):
        """Reset the controller to its initial state."""
        self.first_contact = False
        self.yc_int = 0
        self.θd_int = 0
        self.θp = 0
        self.inc_sign = 1

    def update(self, position, force, dt=0):
        """Compute a new pushing velocity based on contact position and force
        (all expressed in the world frame)."""
        assert len(position) == 2
        assert len(force) == 2

        pathdir, yc = self.path.compute_direction_and_offset(position)
        f_norm = np.linalg.norm(force)

        # bail if we haven't ever made contact yet
        if not self.first_contact:
            if f_norm < self.force_min:
                return self.speed * pathdir
            self.first_contact = True

        θd = util.signed_angle(pathdir, util.unit(force))

        # integrators
        self.yc_int += dt * yc
        self.θd_int += dt * θd

        speed = self.speed

        # pushing angle
        if f_norm < self.force_min:
            # if we've lost contact, try to recover by circling back
            θp = self.θp - self.inc_sign * self.con_inc
        elif f_norm > self.force_max:
            # diverge from the path if force is too high

            # TODO
            # θp = self.θp + self.inc_sign * self.inc
            θp = self.θp + np.sign(θd) * self.div_inc
        else:
            θp = (
                (1 + self.kθ) * θd
                + self.ky * yc  # * (1 + np.abs(yc))
                + self.ki_θ * self.θd_int
                + self.ki_y * self.yc_int
            )
            # α = 1.0
            # θp = (1 - α) * self.θp + α * θp
            # if np.abs(yc) > 1.0:
            #     speed = 0.5 * speed
            self.inc_sign = np.sign(θd)

        self.θp = util.wrap_to_pi(θp)

        # pushing velocity
        pushdir = util.rot2d(self.θp) @ pathdir

        # avoid the walls of the corridor
        if np.abs(yc) >= self.corridor_radius:
            R = util.rot2d(np.pi / 2)
            perp = R @ pathdir
            print("correction!")
            if off > 0 and perp @ pushdir > 0:
                pushdir = util.unit(pushdir - (perp @ pushdir) * perp)
            elif off < 0 and perp @ pushdir < 0:
                pushdir = util.unit(pushdir - (perp @ pushdir) * perp)

        return speed * pushdir
