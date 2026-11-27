#!/bin/sh
# Runs once, on first initialisation of the data volume: a separate database for the test suite.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE DATABASE mandicast_test OWNER "$POSTGRES_USER";
SQL
