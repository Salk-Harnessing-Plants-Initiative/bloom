---
name: Test Coverage Analysis
description: Run test coverage analysis to identify untested code
category: Testing
tags: [test, coverage, quality]
---

# Test Coverage Analysis

Run tests with coverage analysis to identify untested code and ensure quality standards are met.

## Quick Commands

### Flask API Coverage

**⚠️ Note**: Phase 1 (Foundation) does not include test infrastructure. These commands will work after Phase 2 (Testing Infrastructure) is merged.

To verify pytest is installed:

```bash
cd flask && uv run pytest --version
```

Once Phase 2 is complete, use these commands:

```bash
# Run all tests with coverage
cd flask && uv run pytest --cov

# Run tests with coverage and generate HTML report
cd flask && uv run pytest --cov --cov-report=html

# Run tests with coverage and show missing lines
cd flask && uv run pytest --cov --cov-report=term-missing

# Run tests with coverage for specific module
cd flask && uv run pytest --cov=app tests/

# Check if coverage meets threshold (70%)
cd flask && uv run pytest --cov --cov-fail-under=70
```

### View Coverage Reports

```bash
# Open HTML coverage report in browser
open flask/htmlcov/index.html

# View coverage summary in terminal
cat flask/coverage/coverage-summary.json

# View detailed coverage by file
cd flask && uv run coverage report
```

### Future: Web Frontend Coverage

```bash
# Once Jest is configured (Phase 2 of CI/CD):
# Run Next.js tests with coverage
pnpm test:coverage

# Run tests for specific package
pnpm --filter web test:coverage
```

## Understanding Coverage Results

After running coverage, you'll see a table like:

```
File           | % Stmts | % Branch | % Funcs | % Lines | Uncovered Lines
---------------|---------|----------|---------|---------|----------------
app.py         |      85 |       75 |      90 |      85 | 42-45, 89
config.py      |     100 |      100 |     100 |     100 |
videoWriter.py |      60 |       50 |      75 |      60 | 12-20, 55-68
---------------|---------|----------|---------|---------|----------------
TOTAL          |      75 |       68 |      82 |      75 |
```

### Coverage Metrics

- **% Stmts** (Statements): Percentage of code statements executed
- **% Branch** (Branches): Percentage of conditional branches tested (if/else, try/except)
- **% Funcs** (Functions): Percentage of functions called
- **% Lines**: Percentage of code lines executed
- **Uncovered Lines**: Specific line numbers not covered by tests

### Coverage Goals

- **Flask API core logic**: Target 100% (video generation, S3 operations, authentication)
- **Flask API endpoints**: Target 80%+ (HTTP layer, validation, error handling)
- **Overall project**: Current threshold **70%** (enforced in pyproject.toml)

### What to Test (Priority Order)

1. **High priority**:

   - Video generation logic (VideoWriter class)
   - S3 file operations (boto3 interactions)
   - JWT authentication and authorization
   - Database queries and Supabase operations

2. **Medium priority**:

   - API endpoints and route handlers
   - Request validation and error handling
   - Configuration and environment loading

3. **Lower priority**:
   - Logging and metrics (consider integration tests)
   - Static configuration values
   - Simple getter/setter methods

## Coverage Configuration

Coverage is configured in `flask/pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = [
    "--cov=.",
    "--cov-report=term-missing",
    "--cov-report=html",
    "--cov-report=xml",
    "--cov-fail-under=70",
    "-v",
]

[tool.coverage.run]
source = ["."]
omit = [
    "*/tests/*",
    "*/__pycache__/*",
    "*/.venv/*",
]

[tool.coverage.report]
precision = 2
show_missing = true
skip_covered = false
```

## Coverage Files

After running tests with coverage, these files are generated:

- **`htmlcov/`** - HTML coverage report (interactive, shows line-by-line coverage)
- **`coverage/coverage-summary.json`** - Machine-readable summary for CI
- **`.coverage`** - Raw coverage data (SQLite database)
- **`coverage.xml`** - XML format for CI tools

These are gitignored to avoid committing generated files.

## Analyzing Low Coverage

### Finding Untested Code

```bash
# Generate HTML report for visual inspection
cd flask && uv run pytest --cov --cov-report=html

# Open report and look for red lines (uncovered)
open flask/htmlcov/index.html
```

### Common Reasons for Low Coverage

1. **Missing test files**: No tests written for the module
2. **Edge cases not tested**: Only happy path covered
3. **Error handling not tested**: Exception branches not triggered
4. **Dead code**: Unreachable or unused code (consider removing)

### Improving Coverage

```python
# Example: Testing error handling

def test_video_generation_handles_missing_file():
    """Test that VideoWriter raises error for missing input file."""
    writer = VideoWriter(output_path="/tmp/test.mp4")

    with pytest.raises(FileNotFoundError):
        writer.process_image("/nonexistent/image.png")

def test_s3_upload_handles_network_error(mocker):
    """Test S3 upload retry logic on network failure."""
    mock_boto = mocker.patch('boto3.client')
    mock_boto.return_value.upload_file.side_effect = ConnectionError()

    with pytest.raises(ConnectionError):
        upload_to_s3("/tmp/file.png", "bucket", "key")
```

## Continuous Integration

Once Phase 1 CI/CD is merged, GitHub Actions will:

- Run tests with coverage on all PRs
- Fail the build if coverage drops below 70%
- Upload coverage reports for review
- Track coverage trends over time

## Tips for Writing Tests

1. **Test behavior, not implementation**: Focus on what the code does, not how
2. **Use fixtures**: Share test setup with pytest fixtures
3. **Mock external services**: Don't make real S3/Supabase calls in tests
4. **Test edge cases**: Empty inputs, None values, boundary conditions
5. **Test error paths**: Exceptions, validation failures, network errors
6. **Keep tests fast**: Mock slow operations (file I/O, network)

## Common Testing Patterns for Bloom

### Pattern 1: Testing S3 Operations

```python
import pytest
from unittest.mock import MagicMock

def test_s3_upload_success(mocker):
    """Test successful S3 upload."""
    # Mock boto3 client
    mock_s3 = mocker.patch('boto3.client')
    mock_s3.return_value.upload_file = MagicMock()

    # Call upload function
    result = upload_to_s3('/tmp/test.png', 'bloom-images', 'test.png')

    # Verify upload was called
    mock_s3.return_value.upload_file.assert_called_once()
    assert result is True
```

### Pattern 2: Testing Video Generation

```python
def test_video_writer_creates_output_file(tmp_path):
    """Test VideoWriter creates video file."""
    output_path = tmp_path / "output.mp4"

    writer = VideoWriter(str(output_path), fps=30)
    writer.add_frame(create_test_image())
    writer.finalize()

    assert output_path.exists()
    assert output_path.stat().st_size > 0
```

### Pattern 3: Testing API Endpoints

```python
def test_video_endpoint_requires_auth(client):
    """Test video generation endpoint requires authentication."""
    response = client.post('/api/video/generate', json={
        'scanner_id': 123,
        'decimation': 4
    })

    assert response.status_code == 401
    assert 'authentication' in response.json['error'].lower()
```

### Pattern 4: Testing Supabase Integration

```python
def test_fetch_scan_images(mocker):
    """Test fetching scan images from Supabase."""
    # Mock Supabase client
    mock_supabase = mocker.patch('app.supabase')
    mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {'id': 1, 's3_key': 'image1.png'},
        {'id': 2, 's3_key': 'image2.png'},
    ]

    images = fetch_scan_images(scanner_id=123)

    assert len(images) == 2
    assert images[0]['s3_key'] == 'image1.png'
```

## Pytest Plugins Used

Our test suite uses these pytest plugins (defined in `flask/pyproject.toml`):

- **pytest-cov**: Coverage reporting
- **pytest-flask**: Flask app testing helpers
- **pytest-mock**: Mocking fixture (mocker)
- **responses**: Mock HTTP requests
- **faker**: Generate realistic test data

## Running Specific Tests

```bash
# Run tests for specific file
cd flask && uv run pytest tests/test_app.py

# Run specific test function
cd flask && uv run pytest tests/test_app.py::test_video_generation

# Run tests matching pattern
cd flask && uv run pytest -k "video"

# Run tests with verbose output
cd flask && uv run pytest -v

# Run tests and stop on first failure
cd flask && uv run pytest -x
```

## Coverage Best Practices

1. **Don't chase 100% blindly**: Focus on critical paths, not trivial code
2. **Quality over quantity**: Well-designed tests > high coverage number
3. **Use coverage to find gaps**: Treat it as a discovery tool, not a goal
4. **Test public APIs**: Focus on interfaces, not private implementation details
5. **Keep tests maintainable**: Tests should be easy to understand and update
