.PHONY: install ui build dev clean

install:
	uv pip install -e .

ui:
	cd ui && npm install && npm run build

build: install ui

dev-backend:
	uvicorn contextspy.api.main:create_app --factory --reload --port 5173

dev-ui:
	cd ui && npm run dev

clean:
	rm -rf ui/dist ui/node_modules __pycache__ contextspy/**/__pycache__
