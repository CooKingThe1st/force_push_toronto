#!/usr/bin/env python
import numpy as np
from numpy.linalg import norm
import matplotlib.pyplot as plt

from mm2d.model import TopDownHolonomicModel
from mm2d import obstacle, plotter
from mm2d import control
from mm2d.util import rms

import IPython


# robot parameters
L1 = 1
L2 = 1
VEL_LIM = 1
ACC_LIM = 1

DT = 0.1      # simulation timestep (s)
MPC_DT = 0.2
DURATION = 30.0  # duration of trajectory (s)
NUM_HORIZON = 10  # number of time steps for prediction horizon


def unit(a):
    return a / np.linalg.norm(a)


def main():
    N = int(DURATION / DT) + 1

    model = TopDownHolonomicModel(L1, L2, VEL_LIM, acc_lim=ACC_LIM, output_idx=[0, 1])

    Q = np.eye(model.no)
    R = np.eye(model.ni) * 0.1
    controller = control.MPC(model, MPC_DT, Q, R, VEL_LIM, ACC_LIM)
    controller_obs = control.ObstacleAvoidingMPC(model, MPC_DT, Q, R, VEL_LIM, ACC_LIM)

    ts = np.array([i * DT for i in range(N)])
    qs = np.zeros((N, model.ni))
    dqs = np.zeros((N, model.ni))
    us = np.zeros((N, model.ni))
    ps = np.zeros((N, model.no))
    vs = np.zeros((N, model.no))
    pds = np.zeros((N, model.no))

    # initial state
    q = np.array([0, 0, 0.25*np.pi, -0.5*np.pi])
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

    circle_renderer = plotter.CircleRenderer(obs, pc)
    robot_renderer = plotter.TopDownHolonomicRenderer(model, q)
    plot = plotter.RealtimePlotter([robot_renderer, circle_renderer])
    plot.start(limits=[-5, 10, -5, 10], grid=True)

    for i in range(N - 1):
        # experimental controller for aligning and pushing object to a goal
        # point - generates a desired set point pd; the admittance portion
        # doesn't really seem helpful at this point (since we actually *want*
        # to hit/interact with the environment)
        cos_alpha = np.cos(np.pi * 0.25)
        p1 = pc - 0.25 * unit(pg - pc)
        p2 = pc - obs.r * unit(pg - pc)

        n = min(NUM_HORIZON, N - 1 - i)

        # if EE is near push location, use MPC without obstacle avoidance
        # constraints, since we actually want to contact the obstacle to push
        # it.
        cos_angle = unit(p - pc).dot(unit(p1 - pc))
        if cos_angle >= cos_alpha:
            pd = np.tile(p1, n)
            u = controller.solve(q, dq, pd, n)
        else:
            pd = np.tile(p2, n)
            u = controller_obs.solve(q, dq, pd, n, pc)

        # step the model
        q, dq = model.step(q, u, DT, dq_last=dq)
        p = model.forward(q)
        v = model.jacobian(q).dot(dq)

        # obstacle interaction
        f, movement = obs.force(pc, p)
        pc += movement

        # if object is close enough to the goal position, stop
        if np.linalg.norm(pg - pc) < 0.1:
            print('done')
            break

        # record
        us[i, :] = u
        dqs[i+1, :] = dq
        qs[i+1, :] = q
        ps[i+1, :] = p
        pds[i+1, :] = pd[:model.no]
        vs[i+1, :] = v

        # render
        robot_renderer.set_state(q)
        circle_renderer.set_state(pc)
        plot.update()
    plot.done()

    xe = pds[1:, 0] - ps[1:, 0]
    ye = pds[1:, 1] - ps[1:, 1]
    print('RMSE(x) = {}'.format(rms(xe)))
    print('RMSE(y) = {}'.format(rms(ye)))

    plt.figure()
    plt.plot(ts, pds[:, 0], label='$x_d$', color='b', linestyle='--')
    plt.plot(ts, pds[:, 1], label='$y_d$', color='r', linestyle='--')
    plt.plot(ts, ps[:, 0],  label='$x$', color='b')
    plt.plot(ts, ps[:, 1],  label='$y$', color='r')
    plt.grid()
    plt.legend()
    plt.xlabel('Time (s)')
    plt.ylabel('Position')
    plt.title('End effector position')

    plt.figure()
    plt.plot(ts, dqs[:, 0], label='$\\dot{q}_x$')
    plt.plot(ts, dqs[:, 1], label='$\\dot{q}_1$')
    plt.plot(ts, dqs[:, 2], label='$\\dot{q}_2$')
    plt.grid()
    plt.legend()
    plt.title('Actual joint velocity')
    plt.xlabel('Time (s)')
    plt.ylabel('Velocity')

    plt.figure()
    plt.plot(ts, us[:, 0], label='$u_x$')
    plt.plot(ts, us[:, 1], label='$u_1$')
    plt.plot(ts, us[:, 2], label='$u_2$')
    plt.grid()
    plt.legend()
    plt.title('Commanded joint velocity')
    plt.xlabel('Time (s)')
    plt.ylabel('Velocity')

    plt.figure()
    plt.plot(ts, qs[:, 0], label='$q_x$')
    plt.plot(ts, qs[:, 1], label='$q_1$')
    plt.plot(ts, qs[:, 2], label='$q_2$')
    plt.grid()
    plt.legend()
    plt.title('Joint positions')
    plt.xlabel('Time (s)')
    plt.ylabel('Joint positions')

    plt.show()


if __name__ == '__main__':
    main()
