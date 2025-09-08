# Git Tagging Guide for RPI-Streamer

This guide explains how to create and manage git tags in the RPI-Streamer repository.

## What are Git Tags?

Git tags are references that point to specific commits in your repository's history. They are typically used to mark release points (like v1.0, v2.0, etc.) and provide a way to easily reference important milestones in your project.

## Types of Git Tags

### 1. Lightweight Tags
Lightweight tags are simply pointers to specific commits. They don't contain any additional information.

```bash
# Create a lightweight tag at the current commit
git tag v3.00

# Create a lightweight tag at a specific commit
git tag v3.00 8dbb26e
```

### 2. Annotated Tags (Recommended)
Annotated tags are stored as full objects in the Git database and contain additional metadata like the tagger's name, email, date, and a tagging message.

```bash
# Create an annotated tag at the current commit
git tag -a v3.00 -m "RPI-Streamer version 3.00 release"

# Create an annotated tag at a specific commit
git tag -a v3.00 8dbb26e -m "RPI-Streamer version 3.00 release"
```

## Creating Tags for RPI-Streamer

### For Release Versions
Since the README mentions this is RPI-Streamer v3.00, here's how to create appropriate tags:

```bash
# Create an annotated tag for the current version
git tag -a v3.00 -m "RPI-Streamer v3.00 - Complete streaming server with GPS tracking and 4G connectivity"

# Or create a more detailed tag with multiple lines
git tag -a v3.00 -m "RPI-Streamer v3.00

Features:
- Flask-based web application and streaming server
- Automatic 4G connectivity with GPS tracking
- Multi-device management capabilities
- Professional streaming with WiFi management
- Centralized hardware console integration"
```

### For Development Milestones
```bash
# Create tags for development milestones
git tag -a v3.00-beta -m "RPI-Streamer v3.00 Beta Release"
git tag -a v3.00-rc1 -m "RPI-Streamer v3.00 Release Candidate 1"
git tag -a v3.01 -m "RPI-Streamer v3.01 - Bug fixes and improvements"
```

## Managing Tags

### Viewing Tags
```bash
# List all tags
git tag

# List tags with pattern matching
git tag -l "v3.*"

# Show detailed information about a tag
git show v3.00

# List tags with their commit messages
git tag -n
```

### Pushing Tags to Remote Repository

**Important**: Tags are not automatically pushed when you run `git push`. You need to push them explicitly.

```bash
# Push a specific tag
git push origin v3.00

# Push all tags
git push origin --tags

# Push all tags (alternative syntax)
git push --tags
```

### Deleting Tags

#### Delete Local Tags
```bash
# Delete a local tag
git tag -d v3.00
```

#### Delete Remote Tags
```bash
# Delete a remote tag
git push origin --delete v3.00

# Alternative syntax for deleting remote tags
git push origin :refs/tags/v3.00
```

## Best Practices for RPI-Streamer

### 1. Semantic Versioning
Follow semantic versioning (MAJOR.MINOR.PATCH):
- **MAJOR**: Incompatible API changes
- **MINOR**: New functionality in a backwards compatible manner  
- **PATCH**: Backwards compatible bug fixes

```bash
git tag -a v3.0.0 -m "Major release with new streaming architecture"
git tag -a v3.1.0 -m "Added GPS tracking feature"
git tag -a v3.1.1 -m "Fixed WiFi connection bug"
```

### 2. Release Tags
Create tags from stable branches (main) for releases:

```bash
# Switch to main branch first
git checkout main
git pull origin main

# Create release tag
git tag -a v3.00 -m "RPI-Streamer v3.00 Release"
git push origin v3.00
```

### 3. Pre-release Tags
Use suffixes for pre-releases:

```bash
git tag -a v3.01-alpha -m "Alpha version with experimental features"
git tag -a v3.01-beta -m "Beta version for testing"
git tag -a v3.01-rc1 -m "Release candidate 1"
```

### 4. Tag Naming Convention
For this project, consider this naming pattern:
- `v3.00` - Major release
- `v3.01` - Minor update
- `v3.01-hotfix` - Critical bug fix
- `v3.01-beta` - Beta version
- `v3.01-rc1` - Release candidate

## Example Workflow for RPI-Streamer Release

```bash
# 1. Ensure you're on the main branch and up to date
git checkout main
git pull origin main

# 2. Create and push the release tag
git tag -a v3.00 -m "RPI-Streamer v3.00 - Complete streaming server with GPS tracking"
git push origin v3.00

# 3. Verify the tag was created
git tag -l
git show v3.00
```

## Checking Out Specific Versions

Users can check out specific tagged versions:

```bash
# Check out a specific version
git checkout v3.00

# Create a new branch from a tag
git checkout -b hotfix-v3.00 v3.00
```

## Integration with Installation Scripts

The installation scripts in this repository could be updated to use specific tags:

```bash
# Install specific version
git clone --branch v3.00 https://github.com/tfelici/RPI-Streamer.git

# Or checkout specific tag after cloning
git checkout v3.00
```

## Practical Example: Creating v3.00 Tag

Since the README mentions RPI-Streamer v3.00, here's how to create that tag:

```bash
# First, make sure you're on the main branch with the latest stable code
git checkout main
git pull origin main

# Create the v3.00 annotated tag
git tag -a v3.00 -m "RPI-Streamer v3.00

Complete streaming server for Raspberry Pi featuring:
- Flask-based web application and streaming server
- Automatic 4G connectivity with GPS tracking  
- Multi-device management capabilities
- Professional streaming with WiFi management
- Centralized hardware console integration
- Power monitoring with UPS support
- Real-time diagnostics and system management"

# Push the tag to the remote repository
git push origin v3.00

# Verify the tag was created successfully
git tag -l
git show v3.00
```

## Summary

- Use **annotated tags** (`git tag -a`) for releases as they store more metadata
- Always push tags explicitly with `git push origin <tagname>` or `git push --tags`
- Follow semantic versioning for consistency
- Tag stable commits from the main branch for releases
- Use descriptive tag messages that explain what the version includes

This tagging strategy will help users easily identify and install specific versions of RPI-Streamer, and help maintainers track the project's evolution over time.