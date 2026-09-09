#!/bin/sh
# Создаёт базы, которые нужны кроме основной. Запускается один раз при первом старте.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	CREATE DATABASE mlflow;
	CREATE DATABASE ${POSTGRES_DB}_test;
SQL
