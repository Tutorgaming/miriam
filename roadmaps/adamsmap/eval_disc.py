#!/usr/bin/env python3
from bresenham import bresenham
import csv
from functools import reduce
import imageio
from itertools import combinations, product
import networkx as nx
import numpy as np
from scipy.spatial import Delaunay
import pickle
import random
import sys

from adamsmap import (
    dist,
    get_edge_statistics,
    get_random_pos,
    graphs_from_posar,
    is_pixel_free,
    make_edges,
    vertex_path
)
from adamsmap_filename_verification import (
    is_result_file,
    is_eval_file,
    resolve_mapname,
    resolve
)

# how bigger than its size should the robot sense?
SENSE_FACTOR = 1.2


def eval_disc(batch, nn, g, posar, edgew, agent_diameter, v):
    """
    Evaluating a given graph by simulating disc-shaped robots to travel through
    it. The robots move to their individual goals and whenever they would
    collide, they stop based on an arbitrary priority.

    :param batch: a batch of start / goal pairs for agents
    :param nn: how many nearest neighbours to consider when path-planning
    :param g: the graph to plan on (undirected)
    :param posar: poses of the graph nodes
    :param edgew: edge weights of the agents (determining the direction of the
        edges)
    :param agent_diameter: how big is the agents disc
    :param v: speed of travel
    :return: sum of costs, paths
    """
    sim_paths = simulate_paths_indep(batch, edgew, g, nn, posar, v)
    t_end, sim_paths_coll = simulate_paths_and_waiting(sim_paths, agent_diameter)
    return float(sum(t_end)) / batch.shape[0], sim_paths_coll


def get_collisions(positions):
    """
    find agents that are colliding

    :param positions: the positions per agent
    :return: a dict with agents per colliding vertex
    """
    colls = {}
    for i_a, v in enumerate(positions):
        if v != -1:  # -1 if there is no position for some reason
            if v in colls.keys():
                colls[v].append(i_a)
            else:
                colls[v] = [i_a]
    to_delete = list(filter(lambda k: len(colls[k]) == 1, colls.keys()))
    for k in to_delete:
        colls.pop(k)
    return colls


def synchronize_paths(vertex_paths):
    """
    make sure no two agents are at the same vertex at the same time by making them waiting

    :param vertex_paths: the paths to check
    :return: the paths with waiting
    """
    n_agents = len(vertex_paths)
    i_per_agent = [0 for _ in range(n_agents)]
    finished = [False for _ in range(n_agents)]
    out_paths = [[] for _ in range(n_agents)]
    prev_i_per_agent = [-2 for _ in range(n_agents)]
    while not all(finished):
        current_poss = [-1 for _ in range(n_agents)]
        for i_a in range(n_agents):
            if i_per_agent[i_a] >= len(vertex_paths[i_a]):
                finished[i_a] = True
            else:
                current_poss[i_a] = vertex_paths[i_a][i_per_agent[i_a]]
        print("current_poss:" + str(current_poss))
        assert len(get_collisions(current_poss)) == 0, str(current_poss)
        for i_a in range(n_agents):
            if not finished[i_a]:
                out_paths[i_a].append(current_poss[i_a])
        assert prev_i_per_agent != i_per_agent
        prev_i_per_agent = i_per_agent.copy()

        next_poss = [-1 for _ in range(n_agents)]
        print("next_poss:" + str(next_poss))
        next_coll = {1: [0]}
        blocked = [False for _ in range(n_agents)]
        prev_blocked = blocked.copy()
        i = 0
        while len(next_coll) and not all(blocked):
            i += 1
            assert i < 100
            for i_a in range(n_agents):
                if i_per_agent[i_a]+1 < len(vertex_paths[i_a]) and not blocked[i_a]:
                    next_poss[i_a] = vertex_paths[i_a][i_per_agent[i_a]+1]
                elif blocked[i_a]:
                    next_poss[i_a] = current_poss[i_a]
                elif i_per_agent[i_a] + 1 >= len(vertex_paths[i_a]):
                    next_poss[i_a] = -1
                else:
                    assert False
            print("next_poss:" + str(next_poss))
            next_coll = get_collisions(next_poss)
            print("next_coll:" + str(next_coll))
            all_coll = list(reduce(lambda a, b: a + b, next_coll.values(), []))
            all_coll = sorted(all_coll)
            print("all_coll:" + str(all_coll))
            for c in all_coll:
                if not blocked[c] and next_poss[c] != current_poss[c]:
                    blocked[c] = True
                    break
            print("i_per_agent:" + str(i_per_agent))
            print("blocked:" + str(blocked))
            if all(blocked):
                unblock = random.randint(0, n_agents-1)
                blocked[unblock] = False
            print("-"*10)
        for i_a in range(n_agents):
            if not blocked[i_a]:
                i_per_agent[i_a] += 1
        print("i_per_agent:" + str(i_per_agent))
        print("finished:" + str(finished))
        print("="*10)
    return out_paths


def simulate_paths_indep(batch, edgew, g, nn, posar, v):
    """

    :param batch: a batch of start / goal pairs for agents
    :param edgew: edge weights of the agents (determining the direction of the
        edges)
    :param g: the graph to plan on (undirected)
    :param nn: how many nearest neighbours to consider when path-planning
    :param posar: poses of the graph nodes
    :param v: speed of travel
    :return: simulated paths
    """
    sim_paths = []
    vertex_paths = []
    for i_a in range(batch.shape[0]):
        p = vertex_path(g, batch[i_a, 0], batch[i_a, 1], posar)
        vertex_paths.append(p)
    for p in vertex_paths:
        if p is not None:
            coord_p = np.array([posar[i_p] for i_p in p])
            goal = batch[i_a, 1]
            assert goal == p[-1], str(p) + str(batch[i_a])
            sim_path = simulate_one_path(coord_p, v)
            sim_paths.append(np.array(sim_path))
        else:
            print("Path failed !!")
            sim_paths.append(np.array([batch[i_a, 0]]))
    return sim_paths


def simulate_paths_and_waiting(sim_paths, agent_diameter):
    """
    Simulate paths over time and let robots stop if required.

    :param sim_paths: the coordinate based paths
    :param agent_diameter: how big is the agents disc
    :return: times when agents finished, actual paths
    """
    sim_paths_coll = None
    ended = [False for _ in range(agents)]
    waiting = [False for _ in range(agents)]
    i_per_agent = [-1 for _ in range(agents)]
    t_end = [0 for _ in range(agents)]
    prev_i_per_agent = [0 for _ in range(agents)]
    while not all(ended):
        if prev_i_per_agent == i_per_agent:
            print("e:" + str(ended))
            print("ipa:" + str(i_per_agent))
            print("pipa:" + str(prev_i_per_agent))
            print("w:" + str(waiting))
            raise Exception("deadlock")
        prev_i_per_agent = i_per_agent.copy()
        sim_paths_coll, ended, t_end, waiting, i_per_agent = iterate_sim(
            t_end, waiting, i_per_agent, sim_paths, sim_paths_coll, agent_diameter
        )
        # print(i_per_agent)
    return t_end, sim_paths_coll


def iterate_sim(t_end, waiting, i_per_agent, sim_paths, sim_paths_coll,
                agent_diameter):
    # print(sim_paths)
    ended = [sim_paths[i].shape[0] - 1 == i_per_agent[i]
             for i in range(agents)]
    time_slice = np.zeros([agents, 2])
    for i_a in range(agents):
        time_slice[i_a, :] = sim_paths[i_a][i_per_agent[i_a]]
        if ended[i_a]:
            t_end[i_a] = i_per_agent[i_a]
    if sim_paths_coll is None:
        sim_paths_coll = np.array([time_slice, ])
    else:
        sim_paths_coll = np.append(sim_paths_coll,
                                   np.array([time_slice, ]),
                                   axis=0)
    waiting = [False for _ in range(agents)]
    for (a, b) in combinations(range(agents), r=2):
        if dist(time_slice[a, :],
                time_slice[b, :]) < SENSE_FACTOR * agent_diameter:
            if(not ended[a] and not ended[b]):
                waiting[min(a, b)] = True  # if one ended, no one has to wait
    # print("w:" + str(waiting))
    i_per_agent = [i_per_agent[i_a] + (1 if (not waiting[i_a]
                                             and not ended[i_a])
                                       else 0)
                   for i_a in range(agents)]
    return sim_paths_coll, ended, t_end, waiting, i_per_agent


def write_csv(n_agents, paths, paths_type, n_trial, fname):
    """
    Write one set of paths as csv file to be read in ROS

    :param n_agents: How many agents are simulated
    :param paths: The simulated paths
    :param paths_type: The type of algorithm {ev, random, undirected}
    :param n_trial: number of the trial {0 ...}
    :param fname: filename of the underlying result file
    """
    # print(paths.shape)
    assert is_result_file(fname)
    fname_csv = ("res/"
                 + "_".join(resolve(fname))
                 + "n_agents" + str(n_agents)
                 + "_paths_type" + str(paths_type)
                 + "_n_trial" + str(n_trial)
                 + ".csv")
    with open(fname_csv, 'w') as f:
        writer = csv.writer(f, delimiter=' ')
        for t in range(paths.shape[0]):
            line = []
            for agent in range(paths.shape[1]):
                for i in [0, 1]:
                    line.append(paths[t, agent, i])
            writer.writerow(line)


def simulate_one_path(coord_p, v):
    """
    Simulate one agent path through coordinates.

    :param coord_p: the coordinates for the path to be followed
    :param v: speed of travel
    :return: the path in coordinates
    """
    sim_path = []
    i = 1
    current = coord_p[0].copy()
    goal = coord_p[-1].copy()
    while dist(current, goal) > v:
        sim_path.append(current)
        next_p = coord_p[i]
        d_next_p = dist(current, next_p)
        if d_next_p > v:
            delta = v * (next_p - current) / d_next_p
            current = (current + delta).copy()
        else:  # d_next_p < v
            rest = v - d_next_p
            assert (rest < v)
            assert (rest > 0)
            if (i + 1 < len(coord_p)):
                after_next_p = coord_p[i + 1]
                d_after_next_p = dist(after_next_p, next_p)
            else:
                rest = 0
                after_next_p = coord_p[i]
                d_after_next_p = 1
            delta = rest * (after_next_p - next_p) / d_after_next_p
            current = (next_p + delta).copy()
            i += 1
    sim_path.append(goal)
    return sim_path


if __name__ == '__main__':
    fname = sys.argv[1]
    with open(fname, "rb") as f:
        assert is_result_file(fname), "Please call with result file"
        store = pickle.load(f)

    agent_ns = [10, 20, 40]
    res = {}
    for ans in agent_ns:
        res[ans] = {}
        res[ans]["undir"] = []
        res[ans]["rand"] = []
        res[ans]["paths_ev"] = []
        res[ans]["paths_undirected"] = []
        res[ans]["paths_random"] = []

    posar = store['posar']
    N = posar.shape[0]
    edgew = store['edgew']
    im = imageio.imread(resolve_mapname(fname))
    __, ge, pos = graphs_from_posar(N, posar)
    make_edges(N, __, ge, posar, edgew, im)
    print(get_edge_statistics(ge, posar))

    for agents, agent_diameter, i_trial in product(
            agent_ns, [10], range(1)):
        print("agents: " + str(agents))
        print("agent_diameter: " + str(agent_diameter))
        v = .2
        nn = 1
        batch = np.array([
            [random.choice(range(N)),
             random.choice(range(N))] for _ in range(agents)])
        cost_ev, paths_ev = eval_disc(batch, nn, ge,
                                      posar, edgew, agent_diameter, v)
        write_csv(agents, paths_ev, "ev-our", i_trial, fname)

        edgew_undirected = np.ones([N, N])
        g_undirected = nx.Graph()
        g_undirected.add_nodes_from(range(N))
        for e in nx.edges(ge):
            g_undirected.add_edge(e[0],
                                  e[1],
                                  distance=dist(posar[e[0]], posar[e[1]]))
        cost_undirected, paths_undirected = (eval_disc(batch, nn, g_undirected,
                                                       posar, edgew_undirected,
                                                       agent_diameter, v))
        write_csv(agents, paths_undirected, "undirected", i_trial, fname)

        g_random = nx.Graph()
        g_random.add_nodes_from(range(N))
        posar_random = np.array([get_random_pos(im) for _ in range(N)])
        b = im.shape[0]
        fakenodes1 = np.array(np.array(list(
            product([0, b], np.linspace(0, b, 6)))))
        fakenodes2 = np.array(np.array(list(
            product(np.linspace(0, b, 6), [0, b]))))
        tri = Delaunay(np.append(posar_random, np.append(
            fakenodes1, fakenodes2, axis=0), axis=0
        ))
        (indptr, indices) = tri.vertex_neighbor_vertices
        for i_n in range(N):
            neigbours = indices[indptr[i_n]:indptr[i_n + 1]]
            for n in neigbours:
                if (i_n < n) & (n < N):
                    line = bresenham(
                        int(posar_random[i_n][0]),
                        int(posar_random[i_n][1]),
                        int(posar_random[n][0]),
                        int(posar_random[n][1])
                    )
                    # print(list(line))
                    if all([is_pixel_free(im, x) for x in line]):
                        g_random.add_edge(i_n, n,
                                          distance=dist(posar_random[i_n],
                                                        posar_random[n]))
                        g_random.add_edge(n, i_n,
                                          distance=dist(posar_random[i_n],
                                                        posar_random[n]))
        cost_random, paths_random = eval_disc(batch, nn, g_random,
                                              posar_random, edgew_undirected,
                                              agent_diameter, v)
        write_csv(agents, paths_random, "random", i_trial, fname)

        print("our: %d, undir: %d, (our-undir)/our: %.3f%%" %
              (cost_ev, cost_undirected,
               100.*float(cost_ev-cost_undirected)/cost_ev))
        print("our: %d, rand: %d, (our-rand)/our: %.3f%%\n-----" %
              (cost_ev, cost_random,
               100.*float(cost_ev-cost_random)/cost_ev))

        res[agents]["undir"].append(100.*float(
            cost_ev-cost_undirected)/cost_ev)
        res[agents]["rand"].append(100.*float(
            cost_ev-cost_random)/cost_ev)
        res[agents]["paths_ev"].append(paths_ev)
        res[agents]["paths_undirected"].append(paths_undirected)
        res[agents]["paths_random"].append(paths_random)

    fname_write = sys.argv[1] + ".eval"
    assert is_eval_file(fname_write), "Please write "\
        "results to eval file (ending with pkl.eval)"
    with open(fname_write, "wb") as f:
        pickle.dump(res, f)
