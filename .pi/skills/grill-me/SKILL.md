---
name: grill-me
description: Interview the user about a plan or design to resolve open decisions. Use when the user asks to be grilled or wants a design interview, not for a one-shot review.
license: MIT
---

# Grill me

Help the user make decisions, not agree with the agent. Challenge assumptions and
follow dependencies until the important choices are resolved.

## Start

Use the plan already in the conversation or supplied files. Ask only for missing
context. A short description is enough; do not require a formal document.

Establish the goal, constraints, and depth: **shallow** for major choices and
blockers, **deep** for implementation details too. Default to shallow if the user
has not specified. If file tools and relevant code are available, inspect them.
Resolve factual questions from code with file citations; existing code does not
decide what the user should want.

## Interview

1. Sketch a provisional outline of open decisions and their dependencies. Show it
   briefly alongside the first questions; let the user change the scope.
2. Ask up to three related, unblocked questions per round. Ask one when later
   questions depend on its answer. Prioritize decisions that could change the plan.
3. For each question, explain why it matters. Let the user answer first. If they
   ask for recommendations, give a choice, a short reason, and any uncertainty.
4. Wait for answers. Record choices separately from assumptions and proposals.
   Revise the outline as answers expose new branches or resolve several at once.
5. Do not re-ask settled questions unless new evidence changes their premises;
   explain that change. Treat uncertainty as an open item, not a forced decision.

Use concrete scenarios: What happens with missing observations? Does the solver
need to handle a singular system? Which approximation is acceptable at this scale?
Do not manufacture an objection when the evidence supports the plan.

## Finish

Stop when the selected scope is resolved or the user wants to stop. Summarize:

- **Decisions:** choice and rationale.
- **Open items:** uncertainty, risk, and evidence needed.
- **Next action:** the smallest useful step.

Do not create or edit project files unless the user asks. For an interview that
also maintains a glossary and decision records, offer `grill-with-docs` if installed.
