"""Incremental D* Lite (Koenig and Likhachev, AAAI 2002).

The graph supplies successors, predecessors, non-negative costs and a consistent
heuristic. Edge changes repair existing g/rhs state instead of rebuilding a path.
"""
from collections import defaultdict
import heapq
import math

INF=float('inf')


class DStarLite:
    def __init__(self,graph,start,goal,heuristic=None):
        self.graph,self.start,self.goal=graph,start,goal
        self.heuristic=heuristic or (lambda a,b:0.0)
        self.g=defaultdict(lambda:INF);self.rhs=defaultdict(lambda:INF)
        self.rhs[goal]=0.0;self.km=0.0;self.heap=[];self.queued={};self.serial=0
        self.expanded=0;self.repairs=0;self._push(goal)

    def key(self,node):
        value=min(self.g[node],self.rhs[node])
        return value+self.heuristic(self.start,node)+self.km,value

    def _push(self,node):
        self.serial+=1;entry=(self.key(node),self.serial,node)
        self.queued[node]=entry[:2];heapq.heappush(self.heap,entry)

    def _top(self):
        while self.heap and self.queued.get(self.heap[0][2])!=self.heap[0][:2]:heapq.heappop(self.heap)
        return self.heap[0][0] if self.heap else (INF,INF)

    def update_vertex(self,node):
        if node!=self.goal:
            self.rhs[node]=min((self.graph.cost(node,v)+self.g[v] for v in self.graph.successors(node)),default=INF)
        self.queued.pop(node,None)
        if self.g[node]!=self.rhs[node]:self._push(node)

    def changed(self,tails):
        self.repairs+=1
        for node in set(tails):self.update_vertex(node)

    def move_start(self,start):
        self.km+=self.heuristic(self.start,start);self.start=start

    def compute(self,max_expansions=None):
        max_expansions=max_expansions or getattr(self.graph,'max_expansions',1_000_000)
        count=0
        while self._top()<self.key(self.start) or self.rhs[self.start]!=self.g[self.start]:
            if not self.heap:break
            old,_,node=heapq.heappop(self.heap);self.queued.pop(node,None)
            if old<self.key(node):self._push(node)
            elif self.g[node]>self.rhs[node]:
                self.g[node]=self.rhs[node]
                for parent in self.graph.predecessors(node):self.update_vertex(parent)
            else:
                self.g[node]=INF;self.update_vertex(node)
                for parent in self.graph.predecessors(node):self.update_vertex(parent)
            count+=1
            if count>max_expansions:raise RuntimeError('Route search exceeded its configured work limit')
        self.expanded=count
        if len(self.heap)>max(10000,3*len(self.queued)):
            self.heap=[entry for entry in self.heap if self.queued.get(entry[2])==entry[:2]]
            heapq.heapify(self.heap)

    def path(self):
        self.compute()
        if not math.isfinite(self.g[self.start]):return []
        path=[self.start];seen={self.start}
        while path[-1]!=self.goal:
            node=path[-1]
            options=[(self.graph.cost(node,v)+self.g[v],str(v),v) for v in self.graph.successors(node)]
            if not options:return []
            cost,_,next_node=min(options)
            if not math.isfinite(cost) or next_node in seen:return []
            seen.add(next_node);path.append(next_node)
        return path


def astar(graph,start,goal,heuristic=None):
    heuristic=heuristic or (lambda a,b:0.0)
    best={start:0.0};parent={};heap=[(heuristic(start,goal),0,str(start),start)]
    while heap:
        _,distance,_,node=heapq.heappop(heap)
        if distance!=best.get(node):continue
        if node==goal:
            path=[goal]
            while path[-1]!=start:path.append(parent[path[-1]])
            return path[::-1]
        for v in graph.successors(node):
            cost=distance+graph.cost(node,v)
            if cost<best.get(v,INF):
                best[v]=cost;parent[v]=node
                heapq.heappush(heap,(cost+heuristic(v,goal),cost,str(v),v))
    return []
