# Agent context for this repo

This is Shreya Sharma's 16.S893 portfolio/project site (static HTML/CSS/JS,
no build step). The project proposal lives at `project.html`; sub-pages are
`project-background.html`, `project-methods.html`, `project-data.html`,
`project-progress.html`.

## Where decisions and terminology live

- [`CONTEXT.md`](CONTEXT.md) — glossary of terms whose meaning has been
  agreed for this project (e.g., what counts as "hub-and-spoke" vs.
  "point-to-point" here, what "fixed demand" means). Check this before
  assuming a term's definition.
- [`docs/adr/`](docs/adr/) — decision records for choices that are costly to
  reverse or need explanation for a future reader (scope limits, chosen
  metrics, modeling assumptions). Numbered `NNNN-short-title.md`, oldest
  first. Check for an existing record before re-deciding something.

Both are maintained by the `grill-with-docs` skill during design interviews
with the user. Read them at the start of a session if the task touches
project scope, terminology, or a modeling assumption.

## Skills available in this repo

`.pi/skills/` contains `grill-me`, `grill-with-docs`, `fight-me`, and
`reality-check` — sourced from
https://github.com/16S893-AI-for-engineering-research/skills (the class
skill set for 16.S893). Do not edit these except to re-sync from that repo.
