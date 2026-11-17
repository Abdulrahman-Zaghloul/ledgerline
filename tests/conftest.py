import os

# Point everything (including the CLI, which reads the environment) at the test database
# before any application module is imported.
TEST_DATABASE_URL = os.environ.get(
    "LEDGER_TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/ledger_test",
)
os.environ["LEDGER_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["LEDGER_ENVIRONMENT"] = "test"
