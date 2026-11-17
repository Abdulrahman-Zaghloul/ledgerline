"""Use cases. Services decide *what* changes; the caller (request handler, CLI, worker)
decides *when* to commit, so several steps can be saved atomically as one unit of work."""
