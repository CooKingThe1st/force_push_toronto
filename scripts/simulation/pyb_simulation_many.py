#!/usr/bin/env python3
import argparse
import itertools
import pickle
from pathlib import Path
import time

import matplotlib.pyplot as plt
import numpy as np
import pyb_utils
import pybullet as pyb
import seaborn
from spatialmath.base import rotz, r2q
import tqdm
import rospkg

import mobile_manipulation_central as mm
import force_push as fp

import IPython


# Hz
SIM_FREQ = 1000
CTRL_FREQ = 100

# seconds
DURATION = 180

# friction
# slider μ is set to 1
SURFACE_MU = 0.25
OBSTACLE_MU = 0.25

# controller params
PUSH_SPEED = 0.1
Kθ = 0.3
KY = 0.1
Kf = 0.005
CON_INC = 0.05  # NOTE
DIV_INC = 0.1

FORCE_MIN_THRESHOLD = 1
FORCE_MAX_THRESHOLD = 50

# slider params
SLIDER_MASS = 1.0
BOX_SLIDER_HALF_EXTENTS = (0.5, 0.5, 0.06)
CIRCLE_SLIDER_RADIUS = 0.5
CIRCLE_SLIDER_HEIGHT = 0.12
SLIDER_CONTACT_DAMPING = 100
SLIDER_CONTACT_STIFFNESS = 10000
SLIDER_LOW_INERTIA_MULT = 1.0 / 2.0

SLIDER_INIT_POS = np.array([0, 0, 0.06])
PUSHER_INIT_POS = np.array([-0.7, 0, 0.06])

# if the closest distance between pusher and slider exceeds this amount, then
# the trial is considered to have failed
FAILURE_DIST = 1.0

# trial fails if no force occurs for this many seconds
FAILURE_TIME = 20.0

FILTER_TIME_CONSTANT = 0.05

# variable parameters
I_mask = [True, True, True]
μ0s = [0, 0.5, 1.0]
y0s = [-0.4, 0, 0.4]
θ0s = [-np.pi / 8, 0, np.pi / 8]
s0s = [-0.4, 0, 0.4]

I_mask = [False, False, True]
μ0s = [1]
y0s = [-0.4]
θ0s = [-np.pi / 8]
s0s = [0.4]

START_AT_TRIAL = 0


def simulate(sim, pusher, slider, push_controller, force_controller):
    success = True
    r_pw_ws = []
    r_sw_ws = []
    forces = []
    ts = []

    last_force_time = 0

    # smoother = mm.ExponentialSmoother(τ=FILTER_TIME_CONSTANT, x0=np.zeros(3))

    t = 0
    steps = DURATION * SIM_FREQ
    for i in range(DURATION * SIM_FREQ):
        t = sim.timestep * i

        if i % CTRL_FREQ == 0:
            # get contact force and pusher position
            force = pusher.get_contact_force([slider.uid])

            # NOTE this does not work well
            # force = smoother.update(force, sim.timestep)

            if np.linalg.norm(force) >= FORCE_MIN_THRESHOLD:
                last_force_time = t
            r_pw_w = pusher.get_joint_states()[0]
            f = force[:2]

            # generate command
            v_cmd = push_controller.update(position=r_pw_w[:2], force=f)
            v_cmd = force_controller.update(force=f, v_cmd=v_cmd)
            # V_cmd = np.append(v_cmd, 0)

            pusher.command_velocity(v_cmd)

            # record information
            r_pw_ws.append(r_pw_w)
            r_sw_ws.append(slider.get_pose()[0])
            forces.append(force)
            ts.append(t)

            # check if the trial has failed (pusher has lost the slider)
            pts = pyb_utils.getClosestPoints(pusher.uid, slider.uid, distance=10)
            if pts[0].contactDistance > FAILURE_DIST:
                success = False
                print("Pusher and slider too far apart!")
                break

            # check if we've lost contact for too long
            if t - last_force_time > FAILURE_TIME:
                success = False
                print("Loss of contact for too long!")
                break

        sim.step()
        # time.sleep(0.1 / SIM_FREQ)

    ts = np.array(ts)
    r_pw_ws = np.array(r_pw_ws)
    r_sw_ws = np.array(r_sw_ws)
    forces = np.array(forces)
    return ts, r_pw_ws, r_sw_ws, success, forces


def setup_box_slider(position):
    slider = fp.BulletSquareSlider(
        position, mass=SLIDER_MASS, half_extents=BOX_SLIDER_HALF_EXTENTS
    )

    slider_vertices = fp.cuboid_vertices(BOX_SLIDER_HALF_EXTENTS)
    slider_masses = (
        SLIDER_MASS * np.ones(slider_vertices.shape[0]) / slider_vertices.shape[0]
    )
    I_max = fp.point_mass_system_inertia(slider_masses, slider_vertices)
    I_uni = fp.uniform_cuboid_inertia(
        mass=SLIDER_MASS, half_extents=BOX_SLIDER_HALF_EXTENTS
    )
    I_low = SLIDER_LOW_INERTIA_MULT * I_uni
    inertias = [I_low, I_uni, I_max]

    return slider, inertias


def setup_circle_slider(position):
    slider = fp.BulletCircleSlider(
        position, radius=CIRCLE_SLIDER_RADIUS, height=CIRCLE_SLIDER_HEIGHT
    )

    I_max = fp.thin_walled_cylinder_inertia(
        SLIDER_MASS, CIRCLE_SLIDER_RADIUS, CIRCLE_SLIDER_HEIGHT
    )
    I_uni = fp.uniform_cylinder_inertia(
        SLIDER_MASS, CIRCLE_SLIDER_RADIUS, CIRCLE_SLIDER_HEIGHT
    )
    I_low = SLIDER_LOW_INERTIA_MULT * I_uni
    inertias = [I_low, I_uni, I_max]

    return slider, inertias


def setup_straight_path():
    obstacles = None
    return fp.SegmentPath.line(direction=[1, 0]), obstacles


def setup_corner_path(corridor=False):
    path = fp.SegmentPath(
        [
            fp.LineSegment([0.0, 0], [3.0, 0]),
            fp.QuadBezierSegment([3.0, 0], [5.0, 0], [5, 2]),
            fp.LineSegment([5.0, 2], [5.0, 5], infinite=True),
        ],
    )

    if corridor:
        block1 = fp.BulletBlock([1, 6.5, 0.5], [2.5, 5, 0.5], mu=OBSTACLE_MU)
        block2 = fp.BulletBlock([7, 4.5, 0.5], [0.5, 7, 0.5], mu=OBSTACLE_MU)
        block3 = fp.BulletBlock([2.5, -2, 0.5], [4, 0.5, 0.5], mu=OBSTACLE_MU)

        obstacles = [
            fp.LineSegment([-1.5, 1.5], [3.5, 1.5]),
            fp.LineSegment([-1.5, -1.5], [6.5, -1.5]),
            fp.LineSegment([3.5, 4.0], [3.5, 11.5]),
            fp.LineSegment([6.5, -1.5], [6.5, 11.5]),
        ]
    else:
        obstacles = None

    return path, obstacles


def make_urdf_file():
    rospack = rospkg.RosPack()
    path = Path(rospack.get_path("force_push")) / "urdf/urdf/sim_pusher.urdf"
    if not path.parent.exists():
        path.parent.mkdir()

    includes = ["$(find force_push)/urdf/xacro/sim_pusher.urdf.xacro"]
    mm.XacroDoc.from_includes(includes).to_urdf_file(path)
    return path.as_posix()


def main():
    np.set_printoptions(precision=6, suppress=True)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--slider",
        choices=["box", "circle"],
        help="Type of slider to use.",
        default="box",
    )
    parser.add_argument(
        "--environment",
        choices=["straight", "corner", "corridor"],
        help="Which environment to use",
        default="straight",
    )
    parser.add_argument("--save", help="Save data to this file.")
    parser.add_argument("--load", help="Load data from this file.")
    parser.add_argument("--no-gui", action="store_true", help="Disable simulation GUI.")
    args = parser.parse_args()

    if args.load is not None:
        with open(args.load, "rb") as f:
            data = pickle.load(f)
        print(f"Loaded processed data from {args.load}")
        fp.plot_simulation_results(data)
        plt.show()
        return

    sim = mm.BulletSimulation(1.0 / SIM_FREQ, gui=not args.no_gui)
    pyb.changeDynamics(sim.ground_uid, -1, lateralFriction=SURFACE_MU)
    pyb.resetDebugVisualizerCamera(
        cameraDistance=5.6,
        cameraYaw=28,
        cameraPitch=-48.6,
        cameraTargetPosition=[3.66, 0.42, 0.49],
    )

    urdf_path = make_urdf_file()
    pusher = fp.BulletPusher(urdf_path, PUSHER_INIT_POS)
    if args.slider == "box":
        slider, slider_inertias = setup_box_slider(SLIDER_INIT_POS)
    elif args.slider == "circle":
        slider, slider_inertias = setup_circle_slider(SLIDER_INIT_POS)
    slider_inertias = [slider_inertias[i] for i in range(3) if I_mask[i]]

    # disable collisions between pusher base and the slider
    # pyb.setCollisionFilterPair(pusher.uid, slider.uid, -1, -1, enableCollision=0)

    slider.set_contact_parameters(
        stiffness=SLIDER_CONTACT_STIFFNESS, damping=SLIDER_CONTACT_DAMPING
    )
    # pyb.changeDynamics(
    #     pusher.uid,
    #     pusher.contact_joint_idx,
    #     contactDamping=SLIDER_CONTACT_DAMPING,
    #     contactStiffness=SLIDER_CONTACT_STIFFNESS,
    # )

    if args.environment == "straight":
        path, obstacles = setup_straight_path()
    elif args.environment == "corner":
        path, obstacles = setup_corner_path(corridor=False)
    elif args.environment == "corridor":
        path, obstacles = setup_corner_path(corridor=True)

    # somewhat janky: for now, we show both vertices for lines and just the
    # middle one for quadratic bezier segments
    for segment in path.segments:
        if type(segment) is fp.LineSegment:
            v1 = np.append(segment.v1, 0.1)
            pyb_utils.debug_frame_world(0.2, tuple(v1), line_width=3)
        v2 = np.append(segment.v2, 0.1)
        pyb_utils.debug_frame_world(0.2, tuple(v2), line_width=3)

    if obstacles is not None:
        for obstacle in obstacles:
            v1 = np.append(obstacle.v1, 1.0)
            v2 = np.append(obstacle.v2, 1.0)
            pyb_utils.debug_frame_world(0.2, tuple(v1), line_width=3)
            pyb_utils.debug_frame_world(0.2, tuple(v2), line_width=3)

    push_controller = fp.PushController(
        speed=PUSH_SPEED,
        kθ=Kθ,
        ky=KY,
        path=path,
        con_inc=CON_INC,
        div_inc=DIV_INC,
        obstacles=obstacles,
        force_min=FORCE_MIN_THRESHOLD,
        force_max=np.inf,
    )
    force_controller = fp.AdmittanceController(
        kf=Kf, force_max=FORCE_MAX_THRESHOLD, vel_max=PUSH_SPEED
    )

    data = {
        "slider_type": args.slider,
        "environment": args.environment,
        "duration": DURATION,
        "sim_freq": SIM_FREQ,
        "ctrl_freq": CTRL_FREQ,
        "push_speed": PUSH_SPEED,
        "kθ": Kθ,
        "ky": KY,
        "con_inc": CON_INC,
        "div_inc": DIV_INC,
        "force_min": FORCE_MIN_THRESHOLD,
        "force_max": FORCE_MAX_THRESHOLD,
        "inertias": slider_inertias,
        "I_mask": I_mask,
        "y0s": y0s,
        "θ0s": θ0s,
        "s0s": s0s,
        "μ0s": μ0s,
        "path": path,
        "obstacles": obstacles,
        "slider": slider,
        "slider_stiffness": SLIDER_CONTACT_STIFFNESS,
        "slider_damping": SLIDER_CONTACT_DAMPING,
    }

    num_sims = len(slider_inertias) * len(y0s) * len(θ0s) * len(s0s) * len(μ0s)

    all_ts = []
    all_r_pw_ws = []
    all_r_sw_ws = []
    successes = []
    all_forces = []
    parameters = []

    count = 0
    with tqdm.tqdm(total=num_sims, initial=START_AT_TRIAL) as progress:
        for (I, μ0, y0, θ0, s0) in itertools.product(
            slider_inertias, μ0s, y0s, θ0s, s0s
        ):
            # for debugging purposes, it may be useful to fast-forward to a
            # particular trial number
            if count < START_AT_TRIAL:
                count += 1
                continue

            # set the new parameters
            r_pw_w = PUSHER_INIT_POS + [0, s0 + y0, 0]
            r_sw_w = SLIDER_INIT_POS + [0, y0, 0]
            Q_ws = r2q(rotz(θ0), order="xyzs")
            pusher.set_contact_friction(μ0)
            slider.set_inertia_diagonal(I)

            # reset everything to initial states
            pusher.reset(position=r_pw_w)
            slider.reset(position=r_sw_w, orientation=Q_ws)
            push_controller.reset()
            # force controller has no state, so nothing to reset
            sim.step()

            # run the sim
            ts, r_pw_ws, r_sw_ws, success, forces = simulate(
                sim, pusher, slider, push_controller, force_controller
            )
            if not success:
                print(f"Trial {count} failed.")
                print(f"I = {np.diag(I)}\nμ = {μ0}\ny0 = {y0}\nθ0 = {θ0}\ns0 = {s0}")
                IPython.embed()

            all_ts.append(ts)
            all_r_pw_ws.append(r_pw_ws)
            all_r_sw_ws.append(r_sw_ws)
            successes.append(success)
            all_forces.append(forces)
            parameters.append([I, μ0, y0, θ0, s0])

            progress.update(1)
            count += 1

    # parse path points to plot
    d = path.segments[-1].direction
    v = path.segments[-1].v2
    dist = np.max((np.vstack(all_r_sw_ws)[:, :2] - v) @ d)
    dist = max(0, dist)

    r_dw_ws = path.get_plotting_coords(dist=dist)

    data["times"] = all_ts
    data["pusher_positions"] = all_r_pw_ws
    data["slider_positions"] = all_r_sw_ws
    data["successes"] = successes
    data["path_positions"] = r_dw_ws
    data["forces"] = all_forces
    data["parameters"] = parameters

    if args.save is not None:
        with open(args.save, "wb") as f:
            pickle.dump(data, f)
        print(f"Saved processed data to {args.save}")

    fp.plot_simulation_results(data)
    plt.show()


if __name__ == "__main__":
    main()
