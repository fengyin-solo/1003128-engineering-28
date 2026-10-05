.PHONY: install backend frontend seed-check test

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

seed-check:
	cd backend && .venv/bin/python -m app.seed_loader --check

test:
	cd backend && .venv/bin/python -m unittest discover -s tests

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev
