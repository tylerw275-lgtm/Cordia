"""Opt-in marker for handlers that need to know who is acting.

The dispatcher used to pass `acting_user` to every tool. Handlers all accept
`**kw`, so it bound fine — and then several of them forward `**kwargs` verbatim
into strictly-typed functions, which raised TypeError. The loop caught it and
returned `{"error": ...}` to the model, so memory, family, calendar and flight
search were dead for six deploys without a single visible failure.

Passing context only where it was asked for makes that impossible to repeat:
a handler that does not opt in never sees the argument at all.
"""


def wants_actor(handlers: dict) -> dict:
    """Mark every handler in a HANDLERS mapping as needing actor context."""
    for handler in handlers.values():
        handler.wants_actor = True
    return handlers
