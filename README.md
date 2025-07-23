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

## Table of Contents

- [Setup Instructions](#setup-instructions)
- [Local Startup Instructions](#local-startup-instructions)
- [Local Clean up Instructions](#local-clean-up-instructions)
- [Dev Startup Instructions](#dev-startup-instructions)
- [Dev Clean up Instructions](#dev-clean-up-instructions)

## Setup Instructions

1. Install [direnv](https://direnv.net/docs/installation.html).
2. Install the latest version of [uv](https://docs.astral.sh/uv/) using: `curl -LsSf https://astral.sh/uv/install.sh | sh`
3. Clone the `main` branch of the repo (`git clone git@github.com:IDinsight/MCPDemo.git`) and cd into the root directory of the repo.
4. Copy the **root** `.template.env` to `.env` and update the following environment variables in `.env`:
    1. `AUTH_RSA_PASSPHRASE`: Run `openssl rand -base64 48` in terminal to generate a random passphrase.
    2. `CSRF_SECRET_KEY`: Run `openssl rand -base64 32` in terminal to generate a random CSRF secret key.
    3. `PATHS_PROJECT_DIR`: Set this to the absolute path of the root directory of the repo.
    4. `PATHS_SECRETS_DIR`: Set this to `PATHS_PROJECT_DIR/secrets`.
5. Allow `direnv` to load the root environment variables by running `direnv allow`.
6. [OPTIONAL (ONLY IF YOU WANT TO RUN IN DEV ENVIRONMENT)] cd in the cicd/deployment/docker-compose directory of the repo and copy the **docker-compose** `.template.env` to `.env` (still within the docker-compose directory) and update the following environment variables in `.env`:
    1. `AUTH_RSA_PASSPHRASE`: Run `openssl rand -base64 48` in terminal to generate a random passphrase.
    2. `CSRF_SECRET_KEY`: Run `openssl rand -base64 32` in terminal to generate a random CSRF secret key.
    3. `PATHS_PROJECT_DIR`: Set this to the absolute path of the root directory of the repo.
    4. `PATHS_SECRETS_DIR`: Set this to `PATHS_PROJECT_DIR/secrets`.
7. cd into the backend directory of the repo and:
    1. Copy the **backend** `.template.env` to `.env` (still within the backend directory).
    2. Allow `direnv` to load the backend environment variables by running `direnv allow`.

## Local Startup Instructions

1. [OPTIONAL] If you started the dev environment first, then from the root directory, run `make down-dev` to stop all dev environment containers.
2. From the root directory, run `make up-local`. This will initialize the Docker containers for the local environment.
3. cd into the backend directory of the repo.
    1. Run `make fresh-env`. This will create a new virtual environment for the backend and install all dependencies.
    2. Run `source .venv/bin/activate`: This will activate the virtual environment created by `make fresh-env`.
4. **Starting FastAPI**
    1. From the backend directory, run `python src/mcp_demo/entries/fastapi_app.py`: This will start the FastAPI server on `http://localhost:8000`.
    2. Go to [http://localhost:8000/docs](http://localhost:8000/docs) to view and interact with the backend API routes.
5. **Initialize user and client**
    1. Create a new user using the `/user/register-first-user` endpoint in the FastAPI docs. Use the following credentials:
        - `username`: `user1`
        - `password`: `user1`
        - You should see the following response:
          ```json
          {
              "username": "user1",
              "user_id": 1,
              "created_by": 1,
              "recovery_codes": [
                  ...,
              ],
              "scopes": ["admin"]
          }
          ```
    2. Create a new client using the `/client/register-first-client` endpoint in the FastAPI docs. Use the following credentials:
        - `client_id`: `client1`
        - `scopes`: `["admin"]`
        - `secret`: `client1`
        - You should see the following response:
          ```json
          {
              "client_id": "client1",
              "created_by": "client1",
              "created_datetime_utc": ...,
              "is_active": true,
              "scopes": ["admin"],
              "updated_datetime_utc": ...
          }
          ```
6. **Starting Main MCP Server**
    1. From the backend directory, run `python src/mcp_demo/entries/mcp_server_main.py` in another terminal window: This will start the main MCP server on `http://localhost:8100`.
7. **Starting External MCP Server**
    1. From the backend directory, run `python src/mcp_demo/entries/mcp_server_external.py` in another terminal window: This will start the external MCP server on `http://localhost:8200`.
8. **Calling Servers With MCP Client**
    1. From the backend directory, run `python src/mcp_demo/entries/client_call.py --auth-type=bearer --include-external-servers --username=user1 --password=user1` in another terminal window: This will start the MCP client with Bearer authentication and make calls to the main and external MCP servers.
    2. From the backend directory, run `python src/mcp_demo/entries/client_call.py --auth-type=oauth --include-external-servers --username=client1 --password=client1` in another terminal window: This will start the MCP client with OAuth and make calls to the main and external MCP servers.

## Local Clean up Instructions

1. Ctrl-C to stop the FastAPI server, main MCP server, and external MCP server in each of their respective terminal windows.
2. In the backend directory, run `deactivate`. This will exit out of the virtual environment created by `uv`.
3. cd back to the root directory and run `make down-local`. This will stop all local testing containers.
4. [OPTIONAL] In the root directory, run `make clean-docker`. This will remove all Docker images and containers created during the local testing setup. Use this command with caution as it will remove all Docker images and containers, not just those related to this project.

## Dev Startup Instructions
1. [OPTIONAL] If you started the local environment first, then from the root directory, run `make down-local` to stop all local environment containers.
2. From the root directory, run `make up-dev`. This will initialize the Docker containers for the dev environment using Docker compose.
3. cd into the backend directory of the repo.
    1. Run `make fresh-env`. This will create a new virtual environment for the backend and install all dependencies.
    2. Run `source .venv/bin/activate`: This will activate the virtual environment created by `make fresh-env`.
4. **Initialize user and client**
    1. Go to [http://localhost:8000/docs](http://localhost:8000/docs) to view and interact with the backend API routes.
    2. Create a new user using the `/user/register-first-user` endpoint in the FastAPI docs. Use the following credentials:
        - `username`: `user1`
        - `password`: `user1`
        - You should see the following response:
          ```json
          {
              "username": "user1",
              "user_id": 1,
              "created_by": 1,
              "recovery_codes": [
                  ...,
              ],
              "scopes": ["admin"]
          }
          ```
    3. Create a new client using the `/client/register-first-client` endpoint in the FastAPI docs. Use the following credentials:
        - `client_id`: `client1`
        - `scopes`: `["admin"]`
        - `secret`: `client1`
        - You should see the following response:
          ```json
          {
              "client_id": "client1",
              "created_by": "client1",
              "created_datetime_utc": ...,
              "is_active": true,
              "scopes": ["admin"],
              "updated_datetime_utc": ...
          }
          ```
5. **Calling Servers With MCP Client (NB: No External Server Here)**
    1. From the backend directory, run `python src/mcp_demo/entries/client_call.py --auth-type=bearer --username=user1 --password=user1` in another terminal window: This will start the MCP client with Bearer authentication and make calls to the **main server only**.
    2. From the backend directory, run `python src/mcp_demo/entries/client_call.py --auth-type=oauth --username=client1 --password=client1` in another terminal window: This will start the MCP client with OAuth and make calls to the **main server only**.

## Dev Clean up Instructions

1. In the backend directory, run `deactivate`. This will exit out of the virtual environment created by `uv`.
2. cd back to the root directory and run `make down-dev`. This will stop all dev containers.
3. [OPTIONAL] In the root directory, run `make clean-docker`. This will remove all Docker images and containers created during the local testing setup. Use this command with caution as it will remove all Docker images and containers, not just those related to this project.
