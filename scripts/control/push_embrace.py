#!/usr/bin/env python
import numpy as np
import matplotlib.pyplot as plt

from mm2d import models, control, obstacle, plotter
from mm2d.util import rms

import IPython


# robot parameters
L1 = 1
L2 = 1
VEL_LIM = 0.5
ACC_LIM = 1

DT = 0.1      # simulation timestep (s)
MPC_DT = 0.2
DURATION = 30.0  # duration of trajectory (s)
NUM_HORIZON = 10  # number of time steps for prediction horizon


def unit(a):
    return a / np.linalg.norm(a)


def main():
    N = int(DURATION / DT) + 1

    model = models.TopDownHolonomicModel(L1, L2, VEL_LIM, acc_lim=ACC_LIM, output_idx=[0, 1])

    Q = np.eye(model.no)
    R = np.eye(model.ni) * 0.1
    controller = control.MPC2(model, MPC_DT, Q, R, VEL_LIM, ACC_LIM)
    controller_obs = control.ObstacleAvoidingMPC2(model, MPC_DT, Q, R, VEL_LIM, ACC_LIM)

    ts = np.array([i * DT for i in range(N)])
    qs = np.zeros((N, model.ni))
    dqs = np.zeros((N, model.ni))
    us = np.zeros((N, model.ni))
    ps = np.zeros((N, model.no))
    vs = np.zeros((N, model.no))
    pds = np.zeros((N, model.no))

    # initial state
    q = np.array([0, 0, 0, 0.25*np.pi, -0.5*np.pi])
    p = model.forward(q)
    dq = np.zeros(model.ni)
    f = np.zeros(2)

    # obstacle
    pc = np.array([-2., 1.])
    obs = obstacle.Circle(0.5, 1000)

    # goal position
    pg = np.array([5., 0])

    qs[0, :] = q
    ps[0, :] = p
    pds[0, :] = p

    using_obs_controller = True

    goal_renderer = plotter.PointRenderer(pg)
    pm_renderer = plotter.PointRenderer(model.forward_m(q), color='b')
    p1_renderer = plotter.PointRenderer(np.zeros(2), color='g')
    circle_renderer = plotter.CircleRenderer(obs.r, pc)
    robot_renderer = plotter.TopDownHolonomicRenderer(model, q, render_collision=True)
    plot = plotter.RealtimePlotter([robot_renderer, circle_renderer, goal_renderer, pm_renderer, p1_renderer])
    plot.start(limits=[-5, 6, -5, 6], grid=True)

    for i in range(N - 1):
        # experimental controller for aligning and pushing object to a goal
        # point
        cos_alpha = np.cos(np.pi * 0.25)

        p1_depth_max = 0.5 * obs.r
        p1_depth = min(np.linalg.norm(pg - pc), p1_depth_max)

        p1 = pc - (obs.r - p1_depth) * unit(pg - pc)
        p2 = pc - obs.r * unit(pg - pc)
        pm = model.forward_m(q)

        n = min(NUM_HORIZON, N - 1 - i)

        # if EE is near push location, use MPC without obstacle avoidance
        # constraints, since we actually want to contact the obstacle to push
        # it.
        cos_angle = unit(pm - pc).dot(unit(p1 - pc))
        if cos_angle >= cos_alpha:
            if using_obs_controller:
                print('switch to non obs controller')
            using_obs_controller = False
            pd = np.tile(p1, n)
            u = controller.solve(q, dq, pd, n, pc)
        else:
            if not using_obs_controller:
                print('switch to obs controller')
            using_obs_controller = True
            pd = np.tile(p2, n)
            u = controller_obs.solve(q, dq, pd, n, pc)

        # step the model
        q, dq = model.step(q, u, DT, dq_last=dq)
        p = model.forward(q)
        v = model.jacobian(q).dot(dq)

        # obstacle interaction TODO refactor
        f1 = obs.calc_point_force(pc, p)
        xbase, ybase = robot_renderer.calc_base_points(q)
        pb1 = np.array([xbase[1], ybase[1]])
        pb2 = np.array([xbase[2], ybase[2]])
        f2 = obs.calc_line_segment_force(pc, pb1, pb2)
        f, movement = obs.apply_force(f1+f2)
        if pc[0] <= 2:
            pc += movement

        # if object is close enough to the goal position, stop
        # if np.linalg.norm(pg - pc) < 0.01:
        #     print('done')
        #     break

        # record
        us[i, :] = u
        dqs[i+1, :] = dq
        qs[i+1, :] = q
        ps[i+1, :] = p
        pds[i+1, :] = pd[:model.no]
        vs[i+1, :] = v

        # render
        p1_renderer.set_state(p1)
        pm_renderer.set_state(pm)
        robot_renderer.set_state(q)
        circle_renderer.set_state(pc)
        plot.update()
    plot.done()


if __name__ == '__main__':
    main()
