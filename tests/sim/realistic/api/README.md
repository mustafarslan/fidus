# Acme API

Order-management HTTP API. Users authenticate with bearer tokens; orders are stored in Postgres and
cached in Redis. Background workers send notification emails.
