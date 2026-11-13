# Architect configuration sources

Reviewed 2026-11-10. This is a task-specific prompt, not a claim that one public prompt is best.

- [Anthropic: Building effective agents](https://www.anthropic.com/research/building-effective-agents)
  supports an orchestrator that delegates bounded questions and synthesizes the results. Architect
  uses Multica's existing dispatch and persistence rather than a new agent framework.
- [Atlassian: User stories](https://www.atlassian.com/agile/project-management/user-stories)
  informs the focus on user outcomes, understandable scope and manageable units of work.
- [Multica: Create and configure an agent](https://multica.ai/docs/agents-create) and
  [Skills](https://multica.ai/docs/skills) describe native instructions, model settings and explicit
  skill attachments. Existing research-with-sources, architecture-designer and conventions skills
  supplement the self-contained ticket-creation skill when present in the workspace.
- [Claude Opus 5.5](https://platform.claude.com/docs/en/models/opus-5-5/overview) documents model ID
  `claude-opus-5-5`. This agent explicitly selects high effort as requested.

Local source checks: `server/internal/daemon/prompt.go` defines the quick-create single-issue
contract; `server/internal/handler/comment.go` and `agent_permission.go` define native comment
dispatch and originating-user access; `squad_briefing.go` defines delegation and pool behavior.

The imported related-work and spec-writer skills require software-factory artifacts, so they are
not attached to this general ticket-writing role. Duplicate checking and ticket structure live in
ticket-creation. For full research with specialists, use chat or assign an intake issue. Quick-create
captures one issue first under Multica's built-in rules.
