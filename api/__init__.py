"""
The service under test: a small REST device-registry API.

FROZEN as of Day 6. From here the harness is the product; this package changes
only to fix a bug mode, never to add features. Every hour spent making this API
"better" is an hour stolen from the part of the project that demonstrates the
actual skill.

Contents:
    models.py  -- Pydantic models; the shapes that become the contract
    store.py   -- in-memory data + the status state machine
    errors.py  -- one declared error envelope for every failure
    bugs.py    -- six labelled, deliberately seeded defects
    main.py    -- the FastAPI app and its eight endpoints

Final surface: 8 endpoints, 6 seeded bug modes, contract pinned at
spec/openapi.json.
"""