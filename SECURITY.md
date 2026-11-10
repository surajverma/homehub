# Security policy

## Supported versions

Only the latest release gets security fixes. Before reporting, please check that the problem still happens on the newest image (`ghcr.io/surajverma/homehub:latest`).

## Reporting a vulnerability

Please report security problems privately through GitHub: open the [Security tab](https://github.com/surajverma/homehub/security) and choose **Report a vulnerability**. Do not open a public issue or pull request for them.

A useful report says which version you ran, the steps or request that show the problem, and what someone could do with it.

## Scope

HomeHub is built for a home or other trusted network and is not hardened for the public internet (see the security notice in the [README](README.md)). Reports are still welcome for anything one household member, another website or a crafted link could do that they should not be able to, such as cross-site requests, script injection, requests to internal addresses, or reading data without the site password or an API token.
