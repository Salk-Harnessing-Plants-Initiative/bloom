---
name: Cleanup Merged Branch
description: Clean up merged branch and archive OpenSpec change
category: Git Workflow
tags: [git, cleanup, openspec]
---

# Cleanup Merged Branch

Clean up after a PR merge by deleting the feature branch and archiving any associated OpenSpec documentation.

## 1. Verify Merge Status

First, confirm the PR has been merged:

```bash
# List recent merged PRs
gh pr list --state merged --limit 10

# View specific PR status
gh pr view <number>

# Check if your branch was merged
gh pr list --state merged --author @me
```

**Important**: Verify the PR status shows "Merged" before proceeding.

## 2. Switch to Main and Pull

```bash
# Switch to main branch
git checkout main

# Pull latest changes
git pull

# Verify you're up to date
git status
```

Confirm you're on the latest main branch before deleting feature branches.

## 3. Delete Feature Branch

Delete both local and remote tracking references:

```bash
# Delete local branch (safe delete - ensures it's merged)
git branch -d <branch-name>

# Clean up remote tracking branches
git remote prune origin

# Verify branch is deleted
git branch -a | grep <branch-name> || echo "✅ Branch deleted"
```

**Important**:

- Use `-d` (not `-D`) to ensure the branch has been merged
- If `-d` fails, the branch hasn't been merged yet - check PR status

## 4. Archive OpenSpec Change (if applicable)

If this PR was tracked with OpenSpec documentation:

### Check for OpenSpec Directory

```bash
# List active changes
openspec list

# Check if your change exists
ls openspec/changes/<change-name>
```

### Archive the Change

```bash
# Archive using OpenSpec CLI (recommended)
openspec archive <change-name> --yes

# Verify archival
openspec list  # Should not show the change anymore
ls openspec/archive/<change-name>  # Should exist
```

The `openspec archive` command:

- Moves the change to `openspec/archive/`
- Updates spec files with implemented requirements
- Marks the change as completed

### Manual Archive (if needed)

If OpenSpec CLI is not available:

```bash
# Move change to archive
git mv openspec/changes/<change-name> openspec/archive/<change-name>

# Update archive README
# (See template below)
```

### Update Archive README

Add entry to `openspec/archive/README.md`:

```markdown
### <change-name> (Month Year)

**Status**: ✅ Completed - Merged in PR #<number>

Brief description of what was implemented.

- **Proposal**: [proposal.md](<change-name>/proposal.md)
- **Tasks**: [tasks.md](<change-name>/tasks.md)
- **Related Issue**: #<issue-number>

**Key Deliverables**:

- Bullet point summary
- Of main deliverables
- And outcomes

**Timeline**: <actual-time> (vs. <estimated-time> estimate)
```

## 5. Commit Archive Changes

If you manually archived the OpenSpec change:

```bash
# Stage archive changes
git add openspec/archive/<change-name>
git add openspec/archive/README.md

# Commit with descriptive message
git commit -m "chore: archive <change-name> OpenSpec change

Moved completed OpenSpec change to archive after PR #<number> merge.
Updated archive README with summary of deliverables.

Related: #<issue>, PR #<pr-number>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"

# Push to main
git push
```

## 6. Verify Cleanup

Confirm cleanup is complete:

```bash
# Verify branch deleted
git branch -a | grep <branch-name> || echo "✅ Branch deleted"

# Verify OpenSpec archived (if applicable)
openspec list | grep <change-name> || echo "✅ OpenSpec archived"

# Verify main is clean
git status
```

## 7. Summary Checklist

After cleanup, verify:

- ✅ PR confirmed merged
- ✅ Main branch updated (`git pull`)
- ✅ Feature branch deleted locally (`git branch -d`)
- ✅ Remote tracking cleaned up (`git remote prune origin`)
- ✅ OpenSpec change archived (if applicable)
- ✅ Archive README updated (if manual archive)
- ✅ Changes committed and pushed
- ✅ Repository clean and organized

## Common Scenarios

### Scenario 1: Simple Bug Fix (No OpenSpec)

```bash
# 1. Verify merge
gh pr view <number>

# 2. Switch to main and pull
git checkout main
git pull

# 3. Delete branch
git branch -d fix/bug-description

# 4. Clean up remote tracking
git remote prune origin

# Done! ✅
```

### Scenario 2: Feature with OpenSpec Documentation

```bash
# 1. Verify merge
gh pr view <number>

# 2. Switch to main and pull
git checkout main
git pull

# 3. Delete branch
git branch -d feature/new-feature

# 4. Archive OpenSpec
openspec archive new-feature --yes

# 5. Verify
openspec list
git status

# Done! ✅
```

### Scenario 3: Branch Not Yet Merged

```bash
# Attempt to delete
git branch -d feature/my-feature

# Output: error: The branch 'feature/my-feature' is not fully merged.

# Check PR status
gh pr view <number>

# If PR is merged on GitHub but git doesn't know:
git fetch origin
git pull

# Try again
git branch -d feature/my-feature
```

### Scenario 4: Force Delete (Use with Caution)

If you're absolutely sure the branch should be deleted even though it's not merged:

```bash
# Check what commits would be lost
git log main..feature/branch-name

# If you're sure, force delete
git branch -D feature/branch-name
```

**Warning**: This permanently deletes unmerged commits. Only use if you're certain the work is no longer needed.

## Notes

- **OpenSpec automation**: `openspec archive` handles moving files and updating specs automatically
- **Not all PRs have OpenSpec**: Bug fixes and small changes may not - that's okay
- **Safe deletion**: `-d` flag ensures the branch is merged before deleting
- **Preserve history**: Use `git mv` instead of shell `mv` to preserve git history
- **Clean regularly**: Don't let old branches accumulate - clean up after each merge

## Troubleshooting

### Branch Delete Fails

```bash
# Error: not fully merged
git branch -d my-feature
# error: The branch 'my-feature' is not fully merged.

# Solution 1: Verify PR is actually merged
gh pr view <number>

# Solution 2: Fetch latest from remote
git fetch origin
git pull

# Solution 3: Check what would be lost
git log main..my-feature

# Solution 4: Force delete if you're sure
git branch -D my-feature
```

### OpenSpec Archive Fails

```bash
# Error: change not found
openspec archive my-change --yes

# Solution 1: Check change ID
openspec list

# Solution 2: Check directory name
ls openspec/changes/

# Solution 3: Use correct change ID (may have hyphens)
openspec archive my-change-name --yes
```

### Remote Branch Still Exists

```bash
# List remote branches
git branch -r | grep my-feature

# If it still shows, prune remote tracking
git remote prune origin

# If branch still exists on GitHub, delete it
gh pr view <number>  # Check if it should be deleted
git push origin --delete feature/my-feature
```

## Best Practices

1. **Clean up promptly**: Delete branches soon after merge to keep repo tidy
2. **Verify merge first**: Always confirm PR is merged before deleting
3. **Archive OpenSpec**: Document completed work for future reference
4. **Use safe delete**: Use `-d` not `-D` to prevent accidental data loss
5. **Update documentation**: Keep archive README current
6. **Regular pruning**: Run `git remote prune origin` regularly to clean stale refs
