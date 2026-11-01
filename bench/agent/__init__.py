"""The agent package: kernel, loops, model client, checks, traces.

Read in this order: kernel.py (action space) -> tool_loop.py (turn machine) ->
state_loop.py (production observations) -> config.py (what production builds).
No import side effects: nothing here touches the network or the filesystem at import time.
"""
