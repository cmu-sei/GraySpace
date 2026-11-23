# Terrain API

The Terrain API is what GHOSTS and other clients build against: the core and its lifecycle,
slices, instances, jobs, capabilities, the map layer and the CA bundle. This page renders the
committed contract, `openapi/grayspace-v1.json`, for reading. Its **Try it out** is off, since
there's no GraySpace behind this site.

One GraySpace runs several deployments ([Deployments](deployments.md)). Every route but
`/api/v1/deployments` is a deployment's, under its base URL `/deployments/{id}`; the spec's
`servers` say so, and the un-prefixed routes are a deprecated alias of the default deployment.

A running GraySpace serves the same reference at **`/api/docs`** (and each deployment's at
`/deployments/{id}/api/docs`), with the spec at `/api/openapi.json`. There, **Authorize** takes the
server's token (`GRAYSPACE_API_TOKEN`), and
**Try it out** sends GET requests. Writes, such as deploying or destroying the core, are
documented but not sent from the page, unless the backend runs with
`GRAYSPACE_API_DOCS_WRITES=true` (for development). The web UI's own routes aren't part of the
contract; in development they're listed at the backend's `/docs`.

Related schemas: the slice spec is `openapi/grayspace-slice-v1.json`, `/api/v1/events`
messages are `openapi/grayspace-events-v1.json`, and `/api/v1/deployments/events` messages are
`openapi/grayspace-deployments-events-v1.json`. See [Slices](slices.md) for how to write a spec.

<swagger-ui src="openapi/grayspace-v1.json"/>
