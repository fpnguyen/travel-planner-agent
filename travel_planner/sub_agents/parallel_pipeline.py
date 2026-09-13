"""Runs booking and itinerary planning concurrently using ADK's graph/node
Workflow engine (google.adk.workflow) — the fan-out/fan-in primitives that
replace the older ParallelAgent/SequentialAgent "workflow agent" classes.

booking_agent and itinerary_planner_agent don't actually depend on each
other's tool calls (flight searches, POI lookups) — only on how the total
trip budget gets split between them. This graph removes that artificial
sequencing: the coordinator pre-allocates an estimated budget split upfront
(see schemas.TripPlanningRequest), both specialists run at the same time,
a JoinNode blocks until both finish, and plan_merger_agent reconciles their
real combined cost against the user's actual total budget afterward.

How this gets exposed to the coordinator: LlmAgent.tools accepts a BaseNode
directly (not just BaseTool/callables) — ADK's own _convert_tool_union_to_tools
(google/adk/agents/llm_agent.py) auto-wraps any BaseNode that isn't itself a
BaseAgent via NodeTool. So trip_planning_pipeline below just needs to be a
plain item in root_agent's tools=[...] list; no explicit tool wrapper needed.
NodeTool requires the wrapped node to have both a description and an explicit
Pydantic input_schema, hence TripPlanningRequest.

Why booking_agent/itinerary_planner_agent are still safe to reuse elsewhere:
node()/build_node() *clones* an LlmAgent when wrapping it (and sets
mode="single_turn" on the clone) rather than mutating the original — so the
same objects imported here are unaffected if referenced elsewhere.

Why seed_trip_request exists: confirmed by reading BaseAgent._run_impl
directly — an agent used as a graph node ignores its own `node_input`
argument entirely and instead reads whatever conversational content is
already in the session (same as a normal chat turn). So simply fanning
node_input out from START to booking_step/itinerary_step would silently
discard it. seed_trip_request is a small FunctionNode that runs first,
takes the raw TripPlanningRequest as node_input (a function parameter
literally named `node_input` is bound directly to it — confirmed in
FunctionNode._bind_parameters), and returns it as a `types.Content` — one of
FunctionNode's recognized pass-through output types — which appends it to
the session as a real turn. booking_step and itinerary_step then read that
turn like any normal conversational input, each pulling out the fields
relevant to it per their own instructions.
"""

from google.adk.workflow import JoinNode, START, Workflow, node
from google.genai import types as genai_types

from ..schemas import TripPlanningRequest
from ..timeouts import AGENT_TIMEOUT, COORDINATOR_TIMEOUT
from .booking_agent.agent import booking_agent
from .itinerary_planner.agent import itinerary_planner_agent
from .plan_merger.agent import plan_merger_agent


async def _seed_trip_request(node_input: TripPlanningRequest) -> genai_types.Content:
    """Turns the coordinator's structured request into a real conversational
    turn so the agent-based branches below can read it (see module docstring
    for why this hop is necessary)."""
    return genai_types.Content(
        role="user",
        parts=[genai_types.Part.from_text(text=node_input.model_dump_json(indent=2))],
    )


seed_node = node(_seed_trip_request, name="seed_trip_request")
booking_node = node(booking_agent, name="booking_step", timeout=AGENT_TIMEOUT)
itinerary_node = node(itinerary_planner_agent, name="itinerary_step", timeout=AGENT_TIMEOUT)

booking_itinerary_join = JoinNode(
    name="booking_itinerary_join",
    description="Waits for both booking_step and itinerary_step to finish before merging.",
)

merge_node = node(plan_merger_agent, name="plan_merge_step", timeout=AGENT_TIMEOUT)

trip_planning_pipeline = Workflow(
    name="trip_planning_pipeline",
    description=(
        "Given a confirmed destination and complete trip details (dates, budget, "
        "travelers, and each specialist's estimated budget share), runs flight/"
        "transportation booking and day-by-day itinerary planning concurrently, "
        "then reconciles both into one final, budget-checked trip plan. Call this "
        "once you have everything needed — it replaces calling booking and "
        "itinerary planning separately."
    ),
    input_schema=TripPlanningRequest,
    timeout=COORDINATOR_TIMEOUT,
    edges=[
        (START, seed_node, (booking_node, itinerary_node), booking_itinerary_join, merge_node),
    ],
)
