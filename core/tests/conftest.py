"""Databas för testerna som behöver Postgres.

Testerna använder databasen i HOMECONTROL_TEST_DATABASE (till exempel
"host=localhost dbname=homecontrol_test user=homecontrol"). Varje test får
ett eget, tomt schema som tas bort efteråt. Utan variabeln hoppas de
testerna över, utom i CI där de måste köras.
"""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from homecontrol import store


@pytest.fixture
def database() -> Iterator[str]:
    """Anslutningen till ett eget, tomt schema."""
    base = os.environ.get("HOMECONTROL_TEST_DATABASE")
    if not base:
        if os.environ.get("CI"):
            pytest.fail("HOMECONTROL_TEST_DATABASE måste vara satt i CI")
        pytest.skip("HOMECONTROL_TEST_DATABASE är inte satt")
    schema = f"test_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            yield make_conninfo(base, options=f"-c search_path={schema},public")
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def conn(database: str) -> Iterator[psycopg.Connection]:
    """En anslutning till ett schema där tabellerna redan finns."""
    with store.connect(database, attempts=1) as connection:
        store.migrate(connection)
        yield connection
