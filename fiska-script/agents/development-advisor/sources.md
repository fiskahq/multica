# Design sources

Reviewed 2026-11-11. This role is a synthesis for this workspace, not a copied universal prompt.

- [Anthropic: Claude Code best practices](https://code.claude.com/docs/en/best-practices): give
  concrete context, clarify a specification before implementation, use focused skills and define
  verifiable outcomes. Instruction-only boundaries are not filesystem enforcement.
- [Google: code review standard](https://google.github.io/eng-practices/review/reviewer/standard.html):
  prioritize technical evidence, maintainability and progress; distinguish preferences from defects.
- [Google: documentation best practices](https://google.github.io/styleguide/docguide/best_practices.html):
  keep design guidance useful, accurate and proportional to the decision.
- [Google: design tradeoffs](https://google.github.io/building-secure-and-reliable-systems/raw/ch04.html):
  consider security and reliability during design and state meaningful tradeoffs.
- [Multica: mentioning agents](https://multica.ai/docs/mentioning-agents): use native comment
  mentions and reply routing for consultation without changing issue ownership or status.
- [Kiro: Opus 5.5](https://kiro.dev/changelog/models/claude-opus-5-5/) and
  [effort settings](https://kiro.dev/docs/models/effort/): configure the Kiro model and high effort
  through native runtime settings. Saved settings are verified; setup does not make a model call.

Local requirements supply the no-code role boundary, transaction guidance, existing skill choices,
and builder handoff protocol. Read-only context MCPs reduce external write access; this agent still
shares the runtime and repository permissions with other agents. It is not a security sandbox.
