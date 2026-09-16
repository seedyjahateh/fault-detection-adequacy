# Reports are derived artifacts: always regenerate from the JSONL data, never hand-edit.
# On Windows without make, run the commands directly.

.PHONY: test report report-pilot audit-pilot classify pending freeze

classify:
	uv run python -m execution.audit.classify run

pending:
	uv run python -m execution.audit.classify pending

freeze:
	uv run python -m execution.audit.freeze

test:
	uv run pytest -q

report-pilot:
	uv run python -m execution.audit.report --pilot

report:
	uv run python -m execution.audit.report

audit-pilot:
	uv run python -m execution.audit.audit run --projects tqdm luigi keras pandas --commit
