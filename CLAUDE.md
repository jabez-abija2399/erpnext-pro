# ERPNext Pro

A Frappe custom app that adds performance improvements and new features on top of ERPNext v16.

## Rules
- New features go in this app (hooks, overrides, new DocTypes). Don't edit ERPNext's own code for them.
- Changes that must live inside ERPNext or Frappe go to the fork `jabez-abija2399/erpnext`, on a branch
  based on `version-16`, and should be written so they can be sent upstream.
- Target: Frappe/ERPNext `version-16`, Python 3.14, Node 24, MariaDB.
- Measure before optimizing: record the before/after numbers in `docs/perf/`.
- Work happens in Claude Code on the web. Push every finished step; the cloud machine is wiped when the session ends.

## Dev setup
See `docs/dev-setup.md`.
