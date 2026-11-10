# Organizer case workspace

The admin library is `/admin`; creation is `/admin/new`; editing is
`/admin/:id/edit`. The editor's `section` query parameter selects `details`,
`materials` or `reference`, including on direct navigation and reload. The shared
admin session and organizer API authorization remain unchanged.

The library fetches 21 records but displays 20 to determine whether another page
exists. Status filtering is server-side; name search applies to the current page.
There is no fabricated total or always-visible pagination for a single page.

The first metadata edit creates an incomplete draft; later changes autosave while
the editor retains focus and moves to the saved case URL.
Both organizer repositories reuse `FileWorkspaceComponent` and
`RemoteFilesComponent`, also used for participant solutions. They share the file
tree, CodeMirror editor, draft recovery, CRUD and download behavior.

The file-tree header opens an inline filename row. Enter creates the file; Escape
cancels and returns focus to Add file. Creation uses the selected folder or the
active file's folder; the “Файлы” header selects the repository root. Relative
paths can create nested files. Empty, duplicate and conflicting names are rejected
beside the input. Failed writes retain the filename, and ambiguous requests remain
locked for exact retry. Published/frozen workspaces do not expose creation controls.

| Action                       | API                                                                                            |
| ---------------------------- | ---------------------------------------------------------------------------------------------- |
| Create draft                 | `POST /api/v1/admin/cases`                                                                     |
| Edit metadata / visibility   | `PATCH /api/v1/admin/cases/:id`, current `expected_version`                                    |
| Materials                    | `/api/v1/admin/cases/:id/files`, `/files/content`                                              |
| Initialize private reference | `PUT /api/v1/admin/cases/:id/reference-solution?expected_case_version=N`, no body              |
| Reference files              | `/api/v1/admin/cases/:id/reference-solution/files`, `/files/content`                           |
| Confirm readiness            | `PATCH .../reference-solution?expected_case_version=N`, exact reference version and commit SHA |
| Publish                      | `PATCH /api/v1/admin/cases/:id`, only current version and published status                     |

All mutations use idempotency keys. Organizer file writes include the current
case version; reference writes also include the current reference version.
After a file mutation, both parent resources reload before further writes.
Reference changes clear readiness. Existing reference files do not automatically
synchronize with subsequent material changes. Publication requires an explicit
confirmation; it creates the backend's immutable participant-visible materials
snapshot while keeping the reference private.

Ambiguous writes lock their inputs and retain the exact method, path, query, body
and idempotency key for an explicit retry in the current page. Navigation warns
about unsaved drafts and pending operations. Refreshing a file conflict updates
parent versions but preserves the local draft and its original blob SHA. Replacing
a draft with the server file requires confirmation. Metadata conflicts preserve
the form and require an explicit decision before saving against a newer version.
If another session freezes/publishes the case, dirty metadata remains available
for download before the user replaces it with the saved version.

The existing API supports UTF-8 text only (100 files and 1 MiB aggregate per
repository). Binary upload and native Git links are not provided. Published cases
must be archived to edit; after the first participant project, content is frozen
by the backend and remains read-only even after archiving.

The remote OpenAPI supplied by the owner was compared with the local API before
implementation; relevant admin routes and request/response schemas match.
Verification uses the local API and its PostgreSQL/Gitea services. Remote data is
not modified by the tests. See `tests/e2e/inline-file-creation.spec.ts`, `admin-workspace.spec.ts`,
`admin-conflicts.spec.ts`, `case-api-integration.spec.ts` and
`learning-api-integration.spec.ts` for fixture and live-backend checks.
