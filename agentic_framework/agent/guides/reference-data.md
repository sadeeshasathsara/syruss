# Mapping reference data

Admins add data sources: files and APIs holding records the business already keeps (staff lists,
customer or product registers, site lists). Work out how each maps onto the template's identified
objects, the way a person setting up an import would:

- which list holds the records, and what kind of thing each record is;
- which column identifies it, which names it, which hold its attributes;
- which hold other ways it is written (aliases).

One record can describe more than one thing (a staff row that also names a site).

Read the columns and samples with `inspect_source`, check your mapping with a dry run, and look at
what it reports (rows without an id, ids written two ways, names that differ from what is known)
before you propose it. Leave out columns you do not understand rather than guess.

When the data cannot be mapped because the template does not identify that kind of thing, propose
the template change first (read the `template-design` guide). When the choice is genuinely unclear
(two columns that could be the id), ask the admin.
