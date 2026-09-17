"""Blueprint: the stage between a Venture Scout idea and an Engineer build.

Venture Scout answers "what game might be worth building?" and produces an idea.
The Engineer answers "how do I implement this specification?" and produces Luau.
Nothing answered "what exactly should this game be?", so a person wrote the
engineering task by hand.

This package is that middle brain. It takes an audited idea plus what the user
actually wants, proposes features the user selects or rejects, and compiles the
result into a `GameBuildSpecification` -- typed, versioned, immutable once
built from -- which is what the Engineer consumes instead of prose.

The contract that matters: the specification is the contract. A feature the
user rejected is in `excluded_features` and must not reappear, and the Engineer
builds what the spec says and stops.
"""
