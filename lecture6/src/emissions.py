"""
emissions.py — Phase-split fuel burn and emission-mass calculation.

DISCLAIMER
----------
The emission indices in EI_TABLE are ILLUSTRATIVE PLACEHOLDERS only.
They are NOT certified ICAO EEDB values and must NOT be used for
regulatory, policy, or publication purposes.  Representative ICAO LTO
cycle data can be obtained from the ICAO Aircraft Engine Emissions
Databank (https://www.easa.europa.eu/en/domains/environment/icao-aircraft-engine-emissions-databank).

Design notes
------------
* Two operational phases are modelled: LTO (landing and take-off cycle,
  below ~3 000 ft) and cruise (above ~3 000 ft).
* split_phases() allocates fuel_kg into the two phases using a single
  scalar lto_share in [0, 1].
* emissions_kg() computes pollutant mass in kg using:
      mass_kg = EI[g/kg_fuel] * fuel_phase_kg / 1000
  summed over both phases.
"""

from __future__ import annotations

import math
from typing import Tuple, Dict


# ---------------------------------------------------------------------------
# Placeholder emission-index table
# ---------------------------------------------------------------------------
# Units: grams of pollutant per kilogram of fuel burned (g/kg fuel).
#
# These values are ILLUSTRATIVE PLACEHOLDERS derived from rough orders of
# magnitude cited in open aviation-environmental literature.  They do NOT
# correspond to any specific engine model or ICAO-certified measurement.
#
# Typical literature ranges (for context only):
#   NOx LTO:    10–30  g/kg     (higher thrust = higher temperature = more NOx)
#   NOx cruise:  8–18  g/kg
#   CO  LTO:    15–40  g/kg     (incomplete combustion at low power / idle)
#   CO  cruise:  1–5   g/kg
#   HC  LTO:     1–6   g/kg     (unburned hydrocarbons, worst at idle)
#   HC  cruise:  0.1–1 g/kg
#
# PLACEHOLDER VALUES USED HERE — do not treat as data:
EI_TABLE: Dict[str, Dict[str, float]] = {
    "LTO": {
        "NOx": 20.0,   # g/kg — placeholder
        "CO":  25.0,   # g/kg — placeholder
        "HC":   3.0,   # g/kg — placeholder
    },
    "cruise": {
        "NOx": 12.0,   # g/kg — placeholder
        "CO":   2.0,   # g/kg — placeholder
        "HC":   0.5,   # g/kg — placeholder
    },
}


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def split_phases(fuel_kg: float, lto_share: float) -> Tuple[float, float]:
    """Split total fuel burn into LTO and cruise phase masses.

    Parameters
    ----------
    fuel_kg : float
        Total fuel burned on the flight (kg).  Must be >= 0.
    lto_share : float
        Fraction of fuel attributed to the LTO phase.  Must be in [0, 1].
        The cruise fraction is implicitly (1 - lto_share).

    Returns
    -------
    (lto_fuel_kg, cruise_fuel_kg) : tuple of float
        Phase fuel masses in kg.  Their sum equals fuel_kg to within
        floating-point rounding (abs error <= ~2 * eps * fuel_kg).

    Raises
    ------
    ValueError
        If fuel_kg < 0 or lto_share is outside [0, 1] or is not finite.
    """
    if not math.isfinite(fuel_kg) or fuel_kg < 0.0:
        raise ValueError(
            f"fuel_kg must be a finite non-negative number; got {fuel_kg!r}"
        )
    if not math.isfinite(lto_share) or not (0.0 <= lto_share <= 1.0):
        raise ValueError(
            f"lto_share must be a finite value in [0, 1]; got {lto_share!r}"
        )

    lto_fuel_kg: float = fuel_kg * lto_share
    cruise_fuel_kg: float = fuel_kg * (1.0 - lto_share)
    return lto_fuel_kg, cruise_fuel_kg


def emissions_kg(fuel_kg: float, lto_share: float) -> Dict[str, float]:
    """Compute total pollutant masses (kg) emitted over a flight.

    The calculation applies the formula

        mass_kg[p] = (EI_LTO[p] * lto_fuel_kg + EI_cruise[p] * cruise_fuel_kg) / 1000

    where EI values are taken from EI_TABLE and are in g/kg fuel.

    Parameters
    ----------
    fuel_kg : float
        Total fuel burned (kg).  Must be >= 0.
    lto_share : float
        LTO phase fuel fraction in [0, 1] (passed to split_phases).

    Returns
    -------
    dict[str, float]
        Pollutant name -> emission mass in kg.  Contains at least the keys
        "NOx", "CO", "HC".  All values are >= 0.

    Raises
    ------
    ValueError
        Propagated from split_phases if inputs are invalid.
    """
    lto_fuel, cruise_fuel = split_phases(fuel_kg, lto_share)

    result: Dict[str, float] = {}
    for pollutant in EI_TABLE["LTO"]:
        ei_lto = EI_TABLE["LTO"][pollutant]
        ei_cruise = EI_TABLE["cruise"][pollutant]
        # Convert g -> kg: divide by 1000
        mass_kg = (ei_lto * lto_fuel + ei_cruise * cruise_fuel) / 1000.0
        result[pollutant] = mass_kg

    return result
