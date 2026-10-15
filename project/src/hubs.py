"""
project/src/hubs.py
====================
Standalone hub-assignment utility extracted from the routing logic in networks.py.

Public API
----------
hub_of(node, hubs, assign=None) -> str
    Return the hub airport for *node* according to the following rules
    (matching the inner ``hub_of`` closure inside ``build_routes_hub_spoke``):

    1. *node* (uppercased) is in *hubs*  →  return node itself.
    2. *assign* is given and contains an entry for *node*  →  return that
       entry (uppercased); raise ``ValueError`` if the mapped value is not in
       *hubs*.
    3. Otherwise  →  return the first hub from *hubs*.

    Input is case-insensitive; output is always uppercase.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple


def hub_of(
    node: str,
    hubs: Iterable[str],
    assign: Optional[Dict[str, str]] = None,
) -> str:
    """Return the hub airport for *node*.

    Parameters
    ----------
    node:
        Airport code (case-insensitive).
    hubs:
        Ordered collection of hub airport codes (case-insensitive).
        Must be non-empty.
    assign:
        Optional explicit mapping from non-hub airport codes (case-insensitive
        keys) to hub codes (case-insensitive values).  If a value is not in
        *hubs* a ``ValueError`` is raised.

    Returns
    -------
    str
        Uppercase hub airport code.

    Raises
    ------
    ValueError
        If *hubs* is empty, or if *assign* maps *node* to a code that is not
        in *hubs*.
    """
    # Normalise hubs to a tuple of uppercase strings, preserving order.
    hubset: Tuple[str, ...] = tuple(h.upper() for h in hubs)
    if not hubset:
        raise ValueError("At least one hub must be provided")

    node_upper = node.upper()

    # Rule 1: node is a hub → maps to itself.
    if node_upper in hubset:
        return node_upper

    # Rule 2: explicit assign provided and covers this node.
    if assign is not None:
        # Normalise assign keys to uppercase for case-insensitive lookup.
        assign_upper = {k.upper(): v.upper() for k, v in assign.items()}
        if node_upper in assign_upper:
            mapped = assign_upper[node_upper]
            if mapped not in hubset:
                raise ValueError(
                    f"Assigned hub {mapped!r} for {node_upper!r} is not in hubs list {hubset}"
                )
            return mapped

    # Rule 3: fall back to first hub.
    return hubset[0]
