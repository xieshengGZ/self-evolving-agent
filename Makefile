PY := .venv/bin/python

.PHONY: test compile check status map handoff migrate run reload

test:
	$(PY) -m pytest -q

compile:
	$(PY) -m compileall -q app plugins

check: compile test

status:
	$(PY) -m app.cli status

map:
	$(PY) -m app.cli map

handoff:
	$(PY) -m app.cli handoff

migrate:
	$(PY) -m app.migrate

reload:
	$(PY) -m app.cli reload $(PLUGIN)

run:
	$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8088
