# velour evaluation store

![badge](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/ekorman/501428c92df8d0de6805f40fb78b1363/raw/velour-coverage.json)

This repo contains the python [client](client) and [backend api](api) packages.

Docs are [here](https://striveworks.github.io/velour/).

## Dev setup

To ensure formatting consistency, we use [pre-commit](https://pre-commit.com/) to manage git hooks. To install it, run

```shell
pip install pre-commit
pre-commit install
```

## Release process

A release is made by publishing a tag of the form `vX.Y.Z` (e.g. `v0.1.0`). This will trigger a GitHub action that will build and publish the python client to [PyPI](https://pypi.org/project/velour-client/). These releases should be created using the [GitHub UI](https://github.com/Striveworks/velour/releases).

## Tests

There are integration tests, backend unit tests, and backend functional tests.

### CI/CD

All tests are run via GitHub actions on every push.

### Running locally

These can be run locally as follows:

#### Integration tests

1. Install the client: from the `client` directory run

```shell
pip install ".[test]"
```

2. Install the backend: from the `api` directory run

```shell
pip install .[test]
```

3. Setup the backend test env (which requires docker compose): from the `api` directory run

```shell
make test-env
```

4. Run the tests: from the base directory run

```shell
pytest -v integration_tests
```

#### Backend unit tests

1. Install the backend package: from the `api` directory run

```shell
pip install .[test]
```

2. Run the tests: from the `api` directory run

```shell
pytest -v tests/unit-tests
```

#### Backend functional tests

These are tests of the backend that require a running instance of PostGIS to be running. To run these

1. Install the backend package: from the `api` directory run

```shell
pip install .[test]
```

2. Set the environment variaables `POSTGRES_HOST` and `POSTGRES_PASSWORD` to a running PostGIS instance.

3. Run the functional tests: from the `api` directory run

```shell
pytest -v tests/functional-tests/test_client.py
```

## Authentication

The API can be run without authentication (by default) or with authentication provided by [auth0](https://auth0.com/). A small react app (code at `web/`)

### Backend

To enable authentication for the backend either set the environment variables `AUTH_DOMAIN`, `AUTH_AUDIENCE`, and `AUTH_ALGORITHMS` or put them in a file named `.env.auth` in the `api` directory. An example of such a file is

```
AUTH0_DOMAIN="velour.us.auth0.com"
AUTH0_AUDIENCE=***REMOVED***
AUTH0_ALGORITHMS="RS256"
```

### Frontend

For the web UI either set the environment variables `VITE_AUTH0_DOMAIN`, `VITE_AUTH0_CLIENT_ID`, `VITE_AUTH0_CALLBACK_URL` and `VITE_AUTH0_AUDIENCE` or put them in a file named `.env` in the `web` directory. An example of such a file is:

```
VITE_AUTH0_DOMAIN=velour.us.auth0.com
VITE_AUTH0_CLIENT_ID=<AUTH0 CLIENT ID>
VITE_AUTH0_CALLBACK_URL=http://localhost:3000/callback
VITE_AUTH0_AUDIENCE=https://velour.striveworks.us/
```

### Testing auth

All tests mentioned above run without authentication except for `integration_tests/test_client_auth.py`. Running this test requires setting the envionment variables `AUTH0_DOMAIN`, `AUTH0_AUDIENCE`, `AUTH0_CLIENT_ID`, and `AUTH0_CLIENT_SECRET` accordingly.

## Deployment settings

For deploying behind a proxy or with external routing, the environment variable `API_ROOT_PATH` can be set in the backend, which sets the `root_path` arguement to `fastapi.FastAPI` (see https://fastapi.tiangolo.com/advanced/behind-a-proxy/#setting-the-root_path-in-the-fastapi-app)
