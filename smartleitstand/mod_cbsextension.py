import datetime
import os
import logging
import time

from smartleitstand.mod import Module
from smartleitstand.route import Route, Car

from planner.plan import plan


class Cbsext(Module):
    def __init__(self, grid):
        # params
        self.agent_job = ()
        self.agent_idle = ()
        self.paths = ()
        self.grid = grid

        # data
        self.fname = "/tmp/saves.pkl"
        if os.path.exists(self.fname):
            os.remove(self.fname)
        self.planning = False
        self.plan_params = False

    def which_car(self, cars: list, route_todo: Route, routes_queue: list) -> Car:
        self.update_plan(cars, routes_queue)
        assert len(routes_queue) > 0, "No routes to work with"
        for i_route in range(len(routes_queue)):
            if routes_queue[i_route] == route_todo:
                break
        for i_agent in range(len(cars)):
            if len(self.agent_job[i_agent]) > 0:
                if i_route == self.agent_job[i_agent][0]:
                    return cars[i_agent]
        return False

    def new_job(self, cars, routes_queue):
        self.update_plan(cars, routes_queue)

    def update_plan(self, cars, routes_queue):
        if (cars, routes_queue) == self.plan_params:
            return
        if self.planning:
            logging.warning("already planning")
            while (self.planning):
                time.sleep(.01)
        self.planning = True
        agent_pos = []
        for c in cars:
            agent_pos.append(c.toTuple())

        jobs = []
        alloc_jobs = []
        for i_route in range(len(routes_queue)):
            r = routes_queue[i_route]
            jobs.append(r.toJobTuple())
            if r.onRoute:
                alloc_jobs.append((self.get_car_i(cars, r.car), i_route))

        idle_goals = [((10, 10), (50, 20)), ((10, 11), (50, 20),),
                      ((10, 9), (50, 20),)]  # one idle goal 9,7 with P~N(5,.5)

        planning_start = datetime.datetime.now()
        (self.agent_job,
         self.agent_idle,
         self.paths) = plan(agent_pos,
                            jobs,
                            alloc_jobs,
                            idle_goals,
                            self.grid,
                            plot=False,
                            filename=self.fname)
        logging.info("Planning took %.4fs" % (datetime.datetime.now() - planning_start).total_seconds())

        self.plan_params = (cars, routes_queue)  # how we have planned last time
        self.planning = False

    def get_car_i(self, cars: list, car: Car):
        for i_agent in range(len(cars)):
            if car == cars[i_agent]:
                return i_agent