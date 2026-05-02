set dotenv-load := true

test:
    pytest

lint:
    ruff check pgloom_engineering tests

typecheck:
    mypy pgloom_engineering

check:
    ruff check pgloom_engineering tests
    mypy pgloom_engineering
    pytest

migrate:
    pgloom-engineering pgloom db migrate
    pgloom-engineering db migrate
