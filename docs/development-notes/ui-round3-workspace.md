# ui-round3 Workspace development notes

These constraints were derived from GitLab merge requests !29, !32, !33, and !34 before implementing the Workspace.

## !29 - frontend redesign (`4ad3b7ac`)

- Preserve existing Problem, Submission, Contest, and permission behaviour. UI aggregation must not create a second access-control path.
- Use server-side Markdown and MathJax rendering. Do not insert user content with client-side HTML construction.
- Cover every added flow with view tests, including non-code question types and permission guards.

## !32 - RichEditor and Markdown (`e3308451`)

- Keep RichEditor, preview, Markdown table, and math content on the existing rendering pipeline.
- Inline JSON must be serialized server-side and escaped for `<`, `>`, and `&` before use in script tags.
- Do not assume editor DOM or parsed JSON exists. A failure needs a visible, usable fallback.

## !33 - inline test cases (`3982df87`)

- Reuse the existing test-data and submission interfaces. A workspace control is not a substitute for a backend rule.
- Test UI state transitions as well as POST validation.

## !34 - review fixes (`9c29514c`)

- Style only SCSS sources through `resources/style.scss` and `make_style.sh`. Generated CSS is build output and must not be committed.
- Use existing `--oj-*` tokens and scoped selectors. Test dark mode explicitly for inputs, editor surfaces, popovers, buttons, and tables.
- All visible template text uses `_()`; JavaScript text uses gettext/ngettext; update the zh-Hans Django and JavaScript catalogues.
- Never ship a fake control. Connect it to a real endpoint or make it disabled with an explanatory label.
- Enforce permissions and validation on the server. Client-side state is an assistive layer only.
- Static paths use `static()` and never add a hardcoded cache-buster.

## Workspace-specific acceptance rules

- The workspace page opts into a dedicated base-template shell. It may not inherit normal content margins, footer height, or page scrolling.
- Monaco is served from the application static tree. If initialization fails, keep a labelled textarea available for editing and submission.
- Formal submit remains `ProblemSubmit`. Custom Run remains `idepractice` and cannot affect contest score, submission history, or rankings.
- A `submission` query parameter may only expose a submission after the existing `can_see_detail` check.
