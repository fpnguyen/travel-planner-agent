"""Timeout constants for the trip-planning Workflow graph.

Passed directly as the `timeout` kwarg on nodes in
sub_agents/parallel_pipeline.py — real, framework-enforced limits (a node
that overruns raises NodeTimeoutError, handled by ADK's own retry/error
plumbing), not just prompt guidance. Sized generously because
booking_agent/itinerary_planner_agent each make several sequential tool
calls (each its own model + API round trip) before producing a final answer.
"""

AGENT_TIMEOUT = 60  # Single specialist node (booking_step, itinerary_step, plan_merge_step)
COORDINATOR_TIMEOUT = 180  # The whole trip_planning_pipeline Workflow, start to finish
