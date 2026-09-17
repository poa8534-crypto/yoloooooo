"""The execution layer between the backend and Roblox Studio.

`protocol.py` is the contract: a closed set of typed operations, validated on
both sides. `service.py` is the local bridge the Studio plugin polls.

Neither of them decides anything. The Engineer decides what to build, the
plugin holds the DataModel, and this is the wire between them -- which is why
there is no endpoint here that takes a command to run.
"""
