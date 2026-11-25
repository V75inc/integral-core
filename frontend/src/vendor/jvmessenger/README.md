# JVMessenger visitor UI

The visitor builds use JVAgent JVMessenger source at `1605c009096263594726922c8e953965a168a7c3`.
The source files in this directory correspond to paths under upstream
`jvmessenger/src/`. 16 source files match upstream byte for byte.
`integral.patch` records the two locally extended files, Thread and
MarkdownText. The extensions preserve upstream defaults when omitted.

The visitor adapter supplies the authenticated Integral runtime, bounded text
composer options, status and retry controls, and safe Markdown rendering.
Voice, uploads, and the JVAgent session and transport are never configured.
Visitor history and replies use the existing Integral routes.

`thread.css` is the upstream Tailwind 4 stylesheet compiled using the
pinned checkout's PostCSS configuration. The remote Google Fonts import is
omitted for the visitor CSP. Layer names are prefixed to coexist with
Integral's Tailwind 3 processor. The Integral shell styles own page alignment.
