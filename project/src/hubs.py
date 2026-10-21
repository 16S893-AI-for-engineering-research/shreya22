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

assign_hubs(nodes, hubs, distance_fn) -> dict
    Compute a nearest-hub mapping for every node in *nodes*.

    Rules:
    1. A node that IS a hub maps to itself (hub-identity).
    2. Every other node maps to the hub with the smallest great-circle
       distance to it (nearest by ``distance_fn``).
    3. Ties are broken by hub-list order: the first hub in *hubs* that
       achieves the minimum distance wins.

    Parameters
    ----------
    nodes:
        Iterable of airport codes (case-insensitive; output keys are uppercase).
    hubs:
        Ordered iterable of hub airport codes (case-insensitive; output values
        are uppercase).  Order determines tie-breaking.
    distance_fn:
        Callable (code_a: str, code_b: str) -> float returning a non-negative
        great-circle distance in nautical miles.  Must accept uppercase codes.

    Returns
    -------
    dict[str, str]
        Mapping from every node (uppercase) to its nearest hub (uppercase).

    Raises
    ------
    ValueError
        If *hubs* is empty.
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, Optional, Tuple


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


def assign_hubs(
    nodes: Iterable[str],
    hubs: Iterable[str],
    distance_fn: Callable[[str, str], float],
) -> Dict[str, str]:
    """Compute a nearest-hub assignment for every node in *nodes*.

    Rules
    -----
    1. A node that is already a hub maps to itself.
    2. Every other node maps to the hub with the smallest distance
       returned by *distance_fn*.  Ties are broken by hub-list order
       (the first hub in *hubs* that achieves the minimum wins).

    Parameters
    ----------
    nodes:
        Iterable of airport codes.  Case-insensitive; keys in the returned
        dict are always uppercase.
    hubs:
        Ordered iterable of hub airport codes.  Case-insensitive; values in
        the returned dict are always uppercase.  Order determines tie-breaking.
    distance_fn:
        Callable (code_a, code_b) -> float.  Must accept uppercase codes.
        Returns a non-negative great-circle distance in nautical miles.

    Returns
    -------
    dict[str, str]
        Mapping {node_upper: nearest_hub_upper} for every node in *nodes*.

    Raises
    ------
    ValueError
        If *hubs* is empty.
    """
    hubset: Tuple[str, ...] = tuple(h.upper() for h in hubs)
    if not hubset:
        raise ValueError("At least one hub must be provided")
    hub_set_lookup = set(hubset)

    result: Dict[str, str] = {}
    for node in nodes:
        node_upper = node.upper()
        # Rule 1: hub maps to itself
        if node_upper in hub_set_lookup:
            result[node_upper] = node_upper
            continue
        # Rule 2: nearest hub by distance_fn, ties by hub-list order
        best_hub = hubset[0]
        best_dist = distance_fn(node_upper, hubset[0])
        for h in hubset[1:]:
            d = distance_fn(node_upper, h)
            if d < best_dist:  # strict: only update on strictly smaller distance
                best_dist = d
                best_hub = h
        result[node_upper] = best_hub
    return result
