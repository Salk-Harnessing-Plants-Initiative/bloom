---
name: Test-Driven Development
description: Red-green-refactor TDD workflow for the Bloom monorepo's Python services.
category: Development
tags: [tdd, testing, pytest, workflow]
---

# Test-Driven Development (TDD)

Structured TDD workflow for implementing features with tests first, ensuring correctness and code quality across the Bloom monorepo's Python services.

## Purpose

TDD is critical for software where correctness matters. This workflow ensures:

1. Requirements are captured as executable tests before implementation
2. Edge cases are considered upfront (NaN, empty data, boundary conditions, error paths)
3. Calculations and data transforms have known-answer test fixtures
4. Regressions are caught immediately

## Monorepo Context

Bloom is a polyglot monorepo. Python services are managed with `uv` and tested with pytest. Work from **the service/package directory you're working in**, and run tests one of two ways:

```bash
# Per-service: run pytest from inside the service directory
cd bloommcp && uv run pytest                 # FastMCP server
cd langchain && uv run pytest                # LangGraph agent
cd services/video-worker && uv run pytest    # a service under services/

# Root: the root pyproject.toml is `bloom-tests`; run with the test extra
uv run --extra test pytest                   # all wired-up tests from the repo root
```

Throughout this workflow, replace `<service>` with the directory you're working in (e.g. `bloommcp`, `langchain`, `services/video-worker`) and `<module>` with the module under test.

## TDD Cycle

### Phase 1: Red (Write Failing Tests)

Write tests that define the expected behavior of the new feature:

```python
# <service>/tests/test_<module>.py

import pytest


class TestNewFeature:
    """Tests for <feature description>."""

    def test_basic_functionality(self, sample_data):
        """Test that the feature works with normal input."""
        result = new_function(sample_data)
        assert result is not None
        # Assert specific expected values

    def test_edge_case_empty_data(self):
        """Test behavior with empty input."""
        result = new_function([])
        # Assert empty result or appropriate error

    def test_edge_case_missing_values(self, sample_data_with_missing):
        """Test missing/None value handling."""
        result = new_function(sample_data_with_missing)
        # Assert missing values are handled correctly

    def test_known_answer(self):
        """Test with a known-answer fixture for correctness."""
        # Use hand-calculated or reference values
        data = create_known_answer_fixture()
        result = new_function(data)
        assert result == expected_value
```

### Phase 2: Confirm Red

Run the tests to confirm they fail as expected:

```bash
cd <service> && uv run pytest tests/test_<module>.py -v
# or from the repo root:
uv run --extra test pytest <service>/tests/test_<module>.py -v
```

All new tests should fail with `ImportError`, `AttributeError`, or `AssertionError` — not with unexpected errors. If tests fail for the wrong reasons, fix the test setup first.

### Phase 3: Green (Implement the Feature)

Write the minimum code to make all tests pass:

```python
# <service>/<module>.py

def new_function(data):
    """Implement the feature."""
    # Write implementation that satisfies the tests
    ...
```

Run tests again:

```bash
cd <service> && uv run pytest tests/test_<module>.py -v
```

All tests should pass. If not, fix the implementation (not the tests, unless the test itself was wrong).

### Phase 4: Refactor

Improve the implementation while keeping tests green:

1. Clean up code structure
2. Add type hints
3. Improve variable names
4. Extract helper functions if needed

Run tests after each refactor step:

```bash
cd <service> && uv run pytest tests/test_<module>.py -v
```

### Phase 5: Verify Quality

Run the quality check suite for the service. Bloom uses pre-commit (black + ruff + ruff-format + prettier + gitleaks):

```bash
# Formatting and linting for the service you changed
cd <service> && uv run black --check . && uv run ruff check .

# Full test suite for the service (not just new tests)
cd <service> && uv run pytest

# Or run the whole-repo test suite from the root
uv run --extra test pytest

# Run all configured pre-commit hooks (black, ruff, ruff-format, prettier, gitleaks)
uv run pre-commit run --all-files
```

### Phase 6: Commit

Commit with a descriptive message linking the test and implementation:

```bash
git add <service>/<module>.py <service>/tests/test_<module>.py
git commit -m "feat: Add <feature description>

- Tests define expected behavior including edge cases
- Implementation satisfies all test cases
- Known-answer fixtures verify correctness"
```

Pre-commit hooks run automatically on commit. If a hook reformats files or reports an issue, fix it and re-stage before committing.

## Testing Patterns

### Known-Answer Tests

For deterministic logic and transforms, use hand-calculated or reference values:

```python
def test_aggregate_known_answer(self):
    """Verify aggregation with hand-calculated values."""
    records = [{"value": 2}, {"value": 4}, {"value": 6}]
    result = aggregate(records)
    assert result.total == 12
    assert result.mean == 4
```

### Boundary Condition Tests

Test at the edges of valid input:

```python
def test_minimum_input(self):
    """Test with minimum valid input."""
    result = process([{"value": 1}])
    assert result is not None

def test_below_minimum_input(self):
    """Test with too little input."""
    result = process([])
    assert result is None or result.empty
```

### Error Path Tests

Verify failures raise the right errors with useful messages:

```python
def test_invalid_input_raises(self):
    """Invalid input should raise a clear error."""
    with pytest.raises(ValueError, match="must be non-negative"):
        process([{"value": -1}])
```

## Fixture Patterns

### Parametrized Tests

```python
@pytest.mark.parametrize("mode,expected", [
    ("sum", 12),
    ("max", 6),
    ("min", 2),
])
def test_modes(self, mode, expected):
    """Test all aggregation modes produce valid output."""
    result = aggregate([{"value": 2}, {"value": 4}, {"value": 6}], mode=mode)
    assert result == expected
```

### Shared Fixtures

```python
@pytest.fixture
def sample_data():
    """Create sample data for testing."""
    return [{"value": i} for i in range(100)]
```

## Integration

- Run `/lint` during Phase 5 to check code style across the monorepo
- Run `/coverage` to verify test coverage meets threshold
- Run `/run-ci-locally` before committing to ensure the full CI pipeline passes
- Run `/pre-merge` for the comprehensive pre-merge checklist
