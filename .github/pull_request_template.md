## Summary
<!-- 1-3 bullet points describing what changed and why -->

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Enhancement
- [ ] Refactor
- [ ] Documentation
- [ ] CI/CD

## Checklist
- [ ] Code follows project standards (CLAUDE.md)
- [ ] All user-facing text is in Spanish
- [ ] All code/comments/variables in English
- [ ] Type hints on all function signatures
- [ ] No hardcoded magic numbers (config-driven)
- [ ] API clients handle timeouts + rate limits + graceful degradation
- [ ] Tests written with mocked HTTP (no real API calls)
- [ ] `ruff check` passes
- [ ] `ruff format --check` passes
- [ ] `mypy src/` passes
- [ ] `pytest tests/ -x` passes
- [ ] No secrets in code (.env only)
- [ ] Module dependency rules respected (ingestion/processing/alerts don't cross-import)

## Test Plan
<!-- How to verify this change works -->
