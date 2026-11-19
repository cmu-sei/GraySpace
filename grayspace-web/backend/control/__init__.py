"""The GraySpace control plane: several deployments behind one server (docs/deployments.md).

Each deployment is the existing backend (main:app) running as its own worker process, on a unix
socket, in its own work tree with its own Terraform state, inventory, data directory and
environment file. The control plane keeps the registry (/api/v1/deployments), supervises the
workers, and forwards /deployments/{id}/... to them; the un-prefixed /api and /ws routes go to the
default deployment. Nothing here imports the backend's config, which is one deployment's settings.
"""
