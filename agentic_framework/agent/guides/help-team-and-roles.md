# Help: team, invitations and roles

Two kinds of role decide what someone can do: their **organization role** (runs the organization) and their
**workspace role** in each workspace they belong to (opens that workspace's documents). One never grants the other.
Use the page and button names exactly as written here.

## Organization roles
Organization sidebar → **People**: everyone in the organization, with their organization role.
| Role | What it does |
|---|---|
| Owner | Runs the organization: people, workspaces and billing. The only role that can see and pay billing, and close the organization. |
| Admin | Manages people and workspaces. Opens a workspace's documents only by joining it. |
| Member | Belongs to the organization and enters the workspaces they are added to. |
- **Invite people** (on People): they get an email with the organization role you pick; you can copy the link instead.
  Workspace roles are given inside each workspace. **Pending** lists invitations not accepted yet; resending or getting
  a new link replaces the old link.
- Removing someone from the organization ends their access to it and every workspace in it straight away.
- An admin who manages a workspace but isn't a member of it sees **Join as admin** on Workspaces.
- **Activity** (organization sidebar) is the organization-wide audit log, for roles that may see it.

## Workspace roles
In a workspace, Settings → **Members**:
- **People**: everyone with access to this workspace and their role. Removing someone ends their access straight away.
- **Invitations**: invite people by email with the role you pick. Each invitation is a link for that address, valid
  for 7 days.
- **Roles and permissions**: the built-in roles, and custom roles (**Create a role**). A role is a job title: it
  decides what a person can see and do in this workspace.

Built-in workspace roles (they can't be edited):
| Role | What it allows |
|---|---|
| Owner | Everything, including billing and deleting the workspace. |
| Admin | Runs the workspace: settings, members, roles, integrations, the template and its knowledge. |
| Editor | Runs the work: uploads documents, reviews, manages knowledge and analytics screens, and publishes template changes. |
| Reviewer | Reviews documents and answers the agent's questions. |
| Viewer | Sees everything in the workspace, changes nothing. |

The permissions a role can have: See documents and their extracted records · Upload and process documents · Review
documents and answer the agent · Create and edit analytics screens · See the workspace's knowledge and data sources ·
Manage knowledge and data sources · Publish template changes · Chat with the agent · See who is in the workspace and
their roles · Invite people and change their roles · Create and edit custom roles · Manage API keys and webhooks · See
the audit log · Change workspace settings · Manage the plan and billing · Delete the workspace.

## "Why can't I …?"
Almost always the person's role. Say which permission it takes (from the list above) and who can change it: in a
workspace, someone who can "Invite people and change their roles" (Settings → Members → People); for the organization,
an owner or admin (People). Billing is for organization owners only. Never suggest a way around a role.

## Chats with Syruss
Each person's chats with Syruss are private to them. Syruss acts with that person's role: it can't do for them what
their role doesn't allow.
