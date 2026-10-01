# shreya22

Shreya Sharma's 16.S893 portfolio: static HTML/CSS/JS, served without a build step.

## Assignment 2

- Page: [`project-assignment2.html`](project-assignment2.html), also linked in the Project sub-navigation.
- Code and regeneration instructions: [`assignment2/README.md`](assignment2/README.md).
- Skills and test-first evidence: [`assignment2/SKILLS.md`](assignment2/SKILLS.md).
- Technical devlog: [`assignment2/DEVLOG.md`](assignment2/DEVLOG.md).

The first pass reconstructs Sun et al. (2021) Figure 4 from reported aggregates. It exposes source inconsistencies; it is **not** an independent rerun of the paper's network/emissions model. Software tests pass, but strict 0.1% paper agreement does not.

The assignment has its own `uv` project and lockfile. Generated figures/data and the HTML page are pre-generated static assets; GitHub Pages does not run Python. Edit `assignment2/templates/page.html` and regenerate rather than hand-editing the generated assignment page.