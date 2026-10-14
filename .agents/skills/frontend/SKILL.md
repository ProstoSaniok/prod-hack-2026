---
name: frontend
description: "Use when implementing, reviewing, debugging, or testing the frontend of PROD хакатон 2026: Angular 20 standalone UI, Taiga UI, responsive landing pages, assets, accessibility, browser behavior, or frontend data access. Do not use for backend implementation."
argument-hint: "[frontend task]"
user-invocable: true
---

# Frontend skill: PROD хакатон 2026

## Scope

This skill applies only to the browser application in `frontend/`. Do not
modify `backend/` or design backend routes, schemas, persistence, deployment,
or framework-specific server code while using it. If a frontend task needs
server data, define only the smallest client-side contract and keep the
transport replaceable.

The repository currently contains a small Russian-language landing page. Treat
the existing implementation as the source of truth and preserve its visual
language unless the task explicitly changes it:

- Angular 20, standalone components, strict TypeScript and strict templates.
- Taiga UI 4.30 for UI controls; Angular Router is installed, but no routes
  are currently configured.
- RxJS 7.8 and Angular signals are available for state; prefer the simplest
  mechanism that matches the feature.
- Global styling is in `frontend/src/styles.css`; component styling is
  colocated in `src/app/*.css`.
- Tinkoff Sans is served from `frontend/public/fonts/`.
- Public assets are referenced from the root URL, for example
  `/imgs/logos/t_bank_monochrome.svg`.
- The current composition is in `AppComponent`, with the visual reference
  export under `frontend/public/references/pages/main/`. Reference HTML/CSS is
  inspiration and comparison material, not a second application to maintain.
- Production output is served by Nginx with SPA fallback. Client-side routes
  must therefore remain compatible with `try_files ... /index.html`.

## Use this workflow

1. Read the relevant component, global styles, asset paths, and configuration
   before editing. Search for existing components, tokens, handlers, and
   patterns before introducing new ones.
2. Identify the visible behavior and its states: initial, loading, success,
   empty, validation, failure, disabled, and narrow viewport where applicable.
3. Make the smallest coherent change in `frontend/src` and update directly
   related assets or configuration only when needed. Keep presentation in
   templates/styles and behavior/state in TypeScript.
4. Preserve the standalone component model. Add imports explicitly to the
   component that uses a directive, pipe, or component; do not rely on an
   implicit NgModule.
5. Verify the affected viewport and interaction path, then run the narrowest
   applicable command from the validation section.

## Angular and TypeScript rules

- Keep `ChangeDetectionStrategy.OnPush` on components. Prefer immutable inputs,
  signals, computed state, and explicit event handlers over hidden mutation.
- Use strict, meaningful types. Model API data with interfaces or type aliases;
  do not use `any`, broad casts, or non-null assertions to silence errors.
- Keep components focused. Extract a service for reusable state or transport,
  and extract a component when a template has a separate visual/behavioral
  responsibility.
- Use Angular template control flow and bindings consistently with the
  project's Angular version. Avoid direct DOM manipulation unless a browser
  API or an existing Angular abstraction requires it.
- Do not add subscriptions without an ownership strategy. Prefer signals,
  `async` pipe, or lifecycle-aware RxJS interop; if a manual subscription is
  unavoidable, make cleanup explicit.
- Keep `bootstrapApplication` providers in `src/main.ts`. New global providers
  belong there, while feature-specific providers should stay local when
  possible.
- Do not add a route merely to organize a component. When routing is required,
  define typed, lazy-loadable route boundaries and ensure direct navigation
  works with the Nginx SPA fallback.

## UI, visual fidelity, and responsive behavior

- Reuse the existing palette and typography variables before adding values.
  Keep Tinkoff Sans, the neutral paper background, dark ink, gray glass
  surfaces, yellow accent, rounded frame, blur, and layered phone composition
  consistent with the current landing.
- Keep layout CSS in the component stylesheet; reserve `styles.css` for
  global reset, fonts, tokens, and truly global behavior.
- Prefer semantic layout (`flex`, `grid`, intrinsic sizing, `min()`, `max()`,
  `clamp()`) over hardcoded coordinates. Absolute positioning is acceptable
  for the decorative phone/overlay composition when it does not break content
  flow or keyboard access.
- Preserve the existing responsive breakpoints around `1000px` and `620px`
  unless the feature requires a measured change. Check overflow, text wrapping,
  navigation reachability, image cropping, and button hit areas at desktop,
  tablet, and mobile widths.
- Keep decorative layers (`aria-hidden`, `pointer-events: none`) separate from
  interactive content. Do not put meaningful text in pseudo-elements.
- Use Taiga UI controls and their supported inputs/appearances before creating
  a lookalike control. Keep custom styling narrowly scoped to the component.
- Keep motion subtle and optional. Respect `prefers-reduced-motion` for
  non-essential transitions and avoid animation that blocks input or reading.

## Accessibility and content

- Use semantic landmarks and headings in a logical order. Keep the page
  language Russian unless the task explicitly requires another locale.
- Every meaningful image needs useful alternative text; decorative images need
  `alt=""` and must not be the only way to understand an action.
- Use real links for navigation and real buttons for actions. Never implement
  an action as a clickable `div` or a link with a fake `href`.
- Keep visible focus indicators, keyboard operation, sufficient contrast, and
  labels for controls. Do not disable pointer events on an ancestor of an
  interactive element.
- Announce meaningful async status changes without stealing focus. Show
  actionable validation and failure messages in the UI; never hide an error
  behind a console-only fallback.
- Preserve the existing text and correct obvious content/typo issues only when
  they are part of the requested change, so visual and product copy changes do
  not get mixed accidentally.

## Client data access: minimal replaceable contract

The frontend may call a server, but this skill does not prescribe a backend
framework, URL layout, authentication scheme, database, or server DTO format.
Keep a transport adapter behind a small Angular service or equivalent client
boundary. Components should depend on a domain-level method, not on `fetch`,
an URL, or a server response shape.

For each operation document only:

```text
operation: stable client operation name
method: GET | POST | PUT | PATCH | DELETE
path: relative resource path or configurable base URL
query: optional typed query values
body: optional typed request payload
headers: only required client-safe headers
success: typed data needed by the UI
errors: expected status/category mapped to user-facing state
```

Rules for this boundary:

- Keep the base URL configurable; do not hardcode an environment-specific host
  into a component or template.
- Map transport responses to a small UI/domain model in the service. Do not
  leak server envelopes, framework exceptions, or status-code branches into
  templates.
- Encode query/path values with the selected client transport and validate
  user-controlled values before sending them.
- Represent loading, empty, success, and error states explicitly. Preserve
  cancellation and stale-response behavior for search or rapidly changing
  inputs.
- Never log secrets, tokens, personal data, full request bodies, or raw error
  payloads. Never put credentials in source or public assets.
- Treat server data as untrusted: render text through Angular bindings and
  avoid bypassing sanitization.
- If authentication is later required, use the project's approved client
  mechanism and keep token handling out of components; do not invent storage or
  refresh behavior in a UI change.

Example shape (adapt to the chosen transport, do not copy a backend design):

```ts
interface ProductSummary {
  readonly id: string;
  readonly title: string;
}

interface ProductQuery {
  readonly search?: string;
}

// The component uses this domain method; only this service knows the path.
getProducts(query: ProductQuery): Observable<readonly ProductSummary[]>;
```

For a new data-backed feature, define the operation and UI states first, then
implement the adapter. If the server contract is unknown, use an explicit
typed seam or mock at the service boundary rather than guessing a response.

## Assets and project hygiene

- Add reusable static assets under `frontend/public/` and reference them with
  root-relative URLs. Do not import generated reference files into runtime
  bundles without a reason.
- Keep filenames stable and descriptive. Do not duplicate the same logo,
  font, or mockup under `src/assets` and `public`.
- Do not edit `node_modules`, `dist`, `.angular`, generated files, or the
  Figma ZIP to implement a frontend feature. The ZIP and reference export are
  inputs for visual inspection only.
- Keep dependency changes limited to `frontend/package.json` and its lockfile.
  Run `npm ci` when the lockfile or dependency manifest changes.
- Do not add backend checks or backend files to a frontend-only change.

## Validation

Run commands from `frontend/`:

```text
npm ci                 # only after dependency manifest/lockfile changes
npm run build          # required for every code/config change
npm test               # when behavior, templates, services, or utilities change
npm start              # for manual browser/viewport verification
```

For a visual change, manually check the rendered page at desktop, tablet, and
mobile widths, keyboard navigation, focus states, and direct navigation if
routes were added. Treat Angular strict-template errors and build warnings
that affect the changed surface as issues to resolve, not as reasons to weaken
typing. Report what was run and any validation that could not be performed.
