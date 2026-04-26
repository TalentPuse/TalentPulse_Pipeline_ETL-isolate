-- Create additional databases needed by services.
-- PostgreSQL runs scripts in /docker-entrypoint-initdb.d/ alphabetically on first start.

CREATE DATABASE metabase_app;
CREATE DATABASE prefect;
