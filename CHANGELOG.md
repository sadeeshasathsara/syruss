# Changelog

All notable changes to Syruss services.

## Current Environment Versions

| Service | Development (`dev`) | Staging (`test`) | Production (`main`) |
| :--- | :---: | :---: | :---: |
| **admin** | — | — | — |
| **agent** | `0.3.0-dev.1` | `0.2.0-rc.1` | `0.2.0` |
| **server** | `0.3.1-dev.1` | `0.2.0-rc.1` | `0.2.0` |
| **site** | `0.2.1-dev.1` | `0.2.0-rc.1` | `0.2.0` |
| **www** | `0.3.1-dev.1` | `0.2.0-rc.1` | `0.2.0` |

---

## Release History

## [2026-11-04] - Release via dev

### server (0.3.1-dev.1)
- Test environment: a small deploy hook on the test server pulls only the images CI just published

### www (0.3.1-dev.1)
- Build the web app with the public site and account addresses of its environment

## [2026-11-03] - Release via dev

### agent (0.3.0-dev.1)
- Identify documents by a template-defined key (for example site and date) instead of the file name, in lists, notifications, the log and analytics
- The agent proposes the key with the template; it can be changed or combined from the Template page

### server (0.3.0-dev.1)
- Identify documents by a template-defined key (for example site and date) instead of the file name, in lists, notifications, the log and analytics
- The agent proposes the key with the template; it can be changed or combined from the Template page

### site (0.2.1-dev.1)
- Show the loading animation on the sign-in continue page
- Use the application icon as the favicon on the Keycloak sign-in pages

### www (0.3.0-dev.1)
- Documents: the Needs review tab lists the checks and quick decisions waiting for review, each linking to the Review page, with a Go to Review button
- Connections: give the three get-started buttons the same width
- Identify documents by a template-defined key (for example site and date) instead of the file name, in lists, notifications, the log and analytics
- The agent proposes the key with the template; it can be changed or combined from the Template page
- Overview: make Waiting on you full width and show Recent documents beside Latest in the log

## [2026-10-31] - Release via dev

### agent (0.2.0-dev.1)
- Add agent, server, site and www service updates

### server (0.2.0-dev.1)
- Add agent, server, site and www service updates

### site (0.2.0-dev.1)
- Add agent, server, site and www service updates

### www (0.2.0-dev.1)
- Add agent, server, site and www service updates

## [2026-10-26] - Release via dev

### www (0.1.0-dev.21)
- Display entity badges and reference dropdowns in SchemaEditor for relationship template objects

### server (0.1.0-dev.21)
- Add missing SendResult imports across destination adapters

### agent (0.1.0-dev.21)
- Synchronize agent worker release with core API updates

## [2026-10-25] - Release via dev

### site (0.1.0-dev.8)
- Initial landing page and onboarding portal release

### www (0.1.0-dev.8)
- Initial workspace application release

### server (0.1.0-dev.8)
- Initial FastAPI core services and destination adapters

### agent (0.1.0-dev.8)
- Initial agentic framework worker release
