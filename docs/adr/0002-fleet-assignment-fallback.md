# Fleet-assignment fallback: carrier case study if aircraft-ownership data is unavailable

Status: proposed

Preferred: assign aircraft to each route based on which aircraft the
carrier actually operates, if fleet-composition data is available at
sufficient resolution. Fallback: if that data isn't available or is too
coarse, narrow to a case study comparing two carriers that are known to
run different network models (e.g., a legacy hub carrier vs. a
point-to-point / low-cost carrier), and use each carrier's real fleet as
the aircraft-assignment rule for its own network structure.

This fallback trades generality (a US-wide hub-vs-p2p comparison) for
feasibility with available data, and changes the claim from "network
structure X causes AQ outcome Y in general" to "carrier A's network and
fleet produce AQ outcome Y relative to carrier B's" &mdash; a narrower,
carrier-specific comparison. Revisit if fleet data becomes available at
finer resolution than expected.
