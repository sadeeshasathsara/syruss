# Help: documents, review and the template

How documents move through a workspace, and what people do at each step. Use the page and button names exactly as
written here.

## Getting documents in
- **Documents → Upload documents**: drop files in; each is read as soon as it's uploaded.
- Settings → Documents → **Intake** decides whether documents are read as soon as they're uploaded, and which files are
  accepted. Uploads outside these are refused with the reason.
- Documents can also arrive from other systems (Connections → Receive: API, email or SFTP channels; see the
  integrations help guide).
- Uploading needs a role with "Upload and process documents". If a person can't upload, their role doesn't allow it.

## Where a document is (its status)
| Status | What it means |
|---|---|
| Uploaded / Queued | Stored and waiting its turn. |
| Reading | Syruss is reading it now. Its progress shows live. |
| Waiting for answer | Syruss asked a person something it couldn't settle alone; the document waits for the answer. |
| Needs review | Syruss read it but isn't sure enough to save it on its own: a person decides. |
| Done | The record is saved. |
| Failed | It couldn't be read as a whole (not this kind of document, blank, unreadable). The reason is on the document. |

The Documents sidebar has the same views: All documents, Being read, Needs review, Waiting for an answer, Failed, Done.

## Review
- **Review → To review** lists documents that need a person and Syruss's open questions. Opening one shows the
  document, **What Syruss noted** and **What was read**.
- The choices: correct the values and **Save corrections**, **Approve as read**, or **Reject document**. A **Note for
  the agent** (optional) says what it got wrong and how to tell next time; Syruss learns from it.
- **Review → Reviewed** shows the team's decisions and answers.
- Reviewing needs a role with "Review documents and answer the agent".
- When Syruss saves on its own is set in Settings → Documents → **Review rules** (the confidence it needs). Below that,
  the platform makes it send the document to review.

## Agent inbox
**Agent inbox → Waiting on you** holds the conversations Syruss opened and the changes it proposes (to the template,
or merging two people or things that are the same). **Decided** keeps the settled ones. A proposal changes nothing
until a person approves it.

## The template
- **Template → Current version** shows what is captured; **Version history** shows earlier versions.
- **Template → Rules**: checks that run on every record (Syruss can suggest them in the chat; a person adds them).
- **Template → Quality**: how well documents are being read.
- **Refine with samples** (Template, or Design the template): upload new samples and Syruss proposes what to change.
  Publishing a template change needs "Publish template changes".

## Knowledge
**Knowledge → People and things** are the people and things Syruss recognises on the documents, and the other ways
they're written. **Data sources** are the lists you already keep, from files or straight from the systems that hold
them: Syruss works out how each maps and a person approves the import. **What Syruss remembers** is what it has learned
about the business (shared by everyone) and about you (only you see it): correct it, pin what matters, or make it forget.

## The Extraction log
**Extraction log** in the workspace sidebar shows what happened to each document, step by step, as it happens.
Everyone in the workspace can read it; nobody can change it.
