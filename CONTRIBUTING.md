# Contributing

Thanks for your interest in improving Confluence Space Audit.

## Reporting a problem

Open an issue with:

- what you ran (command and options, **without** your API token or site URL if private),
- what you expected and what happened,
- the error message or log (`-v` gives more detail).

## Proposing a change

1. Fork the repository and create a branch from `main`.
2. Install dependencies: `pip install -r requirements.txt -r requirements-dev.txt`
3. Add or update tests in `tests/` for your change.
4. Run the test suite: `PYTHONPATH=. python -m pytest -q`
5. Open a pull request describing the change and why it is useful.

## Guidelines

- The script must stay **read-only** on the Confluence site.
- Never commit credentials, real site URLs or customer data, including in tests and examples.
- Keep dependencies minimal.
