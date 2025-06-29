# MCP Demo
Demo of various MCP features.

<!-- Badges -->
<p style="text-align: center;">
  <a href="https://github.com/econchick/interrogate">
    <img src="./interrogate_badge.svg" alt="Docstring coverage: interrogate">
  </a>
  &nbsp;
  <a href="https://github.com/pylint-dev/pylint">
    <img src="https://img.shields.io/badge/linting-pylint-yellowgreen" alt="Linting: pylint">
  </a>
</p>

## Setup Instructions

1. Copy `cicd/deployment/litellm/.template.env` to `cicd/deployment/litellm/.env` and update:
    1. `OPENAI_API_KEY`: Your OpenAI API key.
2. Copy `backend/.template.env` to `backend/.env`.
3. Copy the **root** `.template.env` to `.env` and update:
    1. `OPENAI_API_KEY`: Your OpenAI API key.
    2. `PATHS_PROJECT_DIR`: The absolute path to the root directory of the project.

## Startup Instructions

1. Install [direnv](https://direnv.net/docs/installation.html).
2. Install the latest version of [uv](https://docs.astral.sh/uv/) using: `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. Clone the repo (`git clone git@github.com:IDinsight/MCPDemo.git`) and cd into the root directory of the repo.
4. Allow `direnv` to load the environment variables by running `direnv allow`.
5. From the root directory, execute `make up-local`. This will start the backend services using Docker.
6. `cd backend`
    1. Allow `direnv` to load the environment variables by running `direnv allow`. This should allow `direnv` to load all environment variables for the backend.
    2. Execute `make fresh-env`. This will create a new virtual environment for the backend and install all dependencies.
    3. Execute `source .venv/bin/activate`: This will activate the virtual environment created by `make fresh-env`.
    4. Execute `python src/mcp_demo/entries/fastapi_app.py`: This will start the FastAPI server on `http://localhost:8000`.
    5. Go to [http://localhost:8000/docs](http://localhost:8000/docs) to view the backend API routes.

## Clean up Instructions

1. In the `backend` directory, execute `deactivate`. This will exit out of the virtual environment created by `uv`.
2. cd back to the root directory and execute `make down-local`. This will stop all backend services.
