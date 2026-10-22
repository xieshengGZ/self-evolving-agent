# Contributing

Thanks for helping. This project is early; small, focused pull requests are easiest to review.

## Development setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
make check
```

`make check` runs `compileall` and the test suite. CI runs the same command on Python 3.12.

## Rules

- Never commit `.env`, `state/`, `backups/`, API keys, or tokens.
- Plugins may only import `app.kernel.api`; do not import `app.main` or other plugins.
- Keep the kernel small. Prefer a plugin when the change is a capability, not a core invariant.
- New or changed plugins need `plugin.json`, the plugin `README.md`, and tests.
- Plan steps should be verifiable. Prefer deterministic checks over "looks done".
- Add a regression test for every bug fix.
- Update `docs/STATUS.md` and `docs/JOURNAL.md` for behavior or architecture changes.
- Version changes follow `docs/VERSIONING.md`.

## Pull requests

Include:

1. What changed and why.
2. How it was tested (`make check`, plus any live check).
3. Risks, especially shell/root/self-update behavior.
4. Screenshots or a short recording for UI changes.

Open an issue before a large refactor or a new dependency.

## Good first issues

Documentation, installer portability, artifact cleanup, test coverage for failure paths, and UI polish are all useful. Look for the `good first issue` label.
