import datetime

import numpy as np

from planner.cbs_ext.plan import plan

grid = np.zeros([10, 10, 51])
grid[4, 2:8, :] = -1

# input
agent_pos = [(1, 1), (9, 1), (3, 1)]  # three agents
jobs = [((1, 6), (9, 6)), ((3, 3), (7, 3)), ((1, 1),)]  # two jobs 1,6 -> 9,1, 3,3 -> 7,3, a simple goal: 1,1

start_time = datetime.datetime.now()

# This misuses the cbsext planner as cbs only planner by fixing the assignment
res_agent_job, res_agent_idle, res_paths = plan(agent_pos, jobs, [], [], grid,
                                                plot=True, filename=None, pathplanning_only=True)

print("computation time:", (datetime.datetime.now() - start_time).total_seconds(), "s")
print(res_agent_job, res_agent_idle, res_paths)
