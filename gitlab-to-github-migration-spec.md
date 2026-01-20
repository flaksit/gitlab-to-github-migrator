# GitLab to GitHub Migration Script Specification

## Overview
Create a Python migration script to migrate a GitLab project to GitHub, preserving all metadata including exact issue/milestone numbers, comments, attachments, and relationships.

## Requirements

### Dependencies
- `python-gitlab` library for GitLab API access
- `PyGithub` library for GitHub API access
- `requests` for file downloads
- `argparse` for CLI parsing
- `logging` for detailed progress tracking

### Command Line Interface
```bash
python gitlab_to_github_migrator.py \
  --gitlab-project "namespace/project-name" \
  --github-repo "org/repo-name" \
  --label-translation "p_*:prio: *" \
  --label-translation "status_*:status: *" \
  --label-translation "t_*:type: *" \
  --label-translation "comp_*:comp: *" \
  --local-clone-path "/path/to/local/clone"
```

**Required Arguments:**
- `--gitlab-project`: GitLab project path (namespace/project)
- `--github-repo`: GitHub repository path (org/repo)

**Optional Arguments:**
- `--label-translation`: Label translation patterns (can be specified multiple times)
- `--local-clone-path`: Path to existing local git clone of GitLab project (if not provided, creates temporary clone)

**Label Translation Pattern:**
Format: `"source_pattern:target_pattern"`
- Use `*` as wildcard
- Example: `"p_*:prio: *"` translates `p_high` → `prio: high`

## Core Functionality

### 1. Repository Creation and Content Migration

#### `create_github_repository(github_org, repo_name, gitlab_project)`
- Create GitHub repository using PyGithub
- Copy description from GitLab project
- Set private visibility, enable issues
- Return repository object
- Handle case where repository already exists (error)

#### `migrate_repository_content(local_clone_path, github_repo)`
- If local_clone_path provided: Use existing local clone
- If local_clone_path not provided: Create temporary clone from GitLab
- Add GitHub remote to local clone
- Push all branches and tags to GitHub
- Verify all content transferred successfully
- Clean up temporary clone if created

### 2. Label Management

#### `handle_labels(gitlab_project, github_repo, label_translations)`
- Fetch all GitLab project labels
- Check existing GitHub organization default labels
- Apply translation patterns from CLI arguments:
  - Parse patterns like `"p_*:prio: *"`
  - Transform GitLab labels using patterns
  - Example: `p_high` + pattern `"p_*:prio: *"` → `prio: high`
- Create only missing translated labels (don't duplicate org defaults)
- Preserve original colors and descriptions
- Return label mapping dictionary: `{gitlab_label: github_label}`

### 3. Milestone Migration with Number Preservation

#### `migrate_milestones_with_number_preservation(gitlab_project, github_repo)`
**Algorithm:**
1. Fetch all GitLab milestones (including closed ones), sort by milestone number
2. Iterate from #1 to highest GitLab milestone number:
   - **If gap detected**: Create placeholder milestone with title "Placeholder Milestone"
   - **If GitLab milestone exists**: Create GitHub milestone with:
     - Original title and description
     - Due dates if set
     - Open/closed state
   - **After each creation**: Verify GitHub milestone number matches expected number
   - **If verification fails**: ABORT with clear error message
3. Return milestone mapping dictionary: `{gitlab_milestone_id: github_milestone_number}`

### 4. Issue Migration with Number Preservation

#### `migrate_issues_with_number_preservation(gitlab_project, github_repo, label_mapping, milestone_mapping)`
**Algorithm:**
1. Fetch all GitLab issues (including closed ones), sort by issue number
2. Iterate from #1 to highest GitLab issue number:
   - **If gap detected**: Create placeholder issue with title "Placeholder"
   - **If GitLab issue exists**: Create complete GitHub issue:
     
     **Issue Content:**
     - **Title**: Use original GitLab title exactly
     - **Description**: Include original description with:
       - All embedded images/attachments downloaded and re-uploaded to GitHub
       - Author information and original timestamps at top
       - Original GitLab issue URL reference
     - **Labels**: Apply translated labels using label_mapping
     - **State**: Set as open/closed matching GitLab state
     - **Milestone**: Link using milestone_mapping
     
     **Comments Migration:**
     - For each GitLab comment/discussion:
       - Migrate comment text with author info and timestamps
       - Download and re-upload any files/images from comments
       - Include system notes (like issue moves) as regular comments
       - Preserve comment threading where possible
     
     **Cross-references**: Preserve original cross-references (automatically handles parent/child relationships when numbers match)

   - **After each creation**: Verify GitHub issue number matches expected using GitHub API
   - **If verification fails**: ABORT with clear error message

### 5. File and Attachment Handling

#### `download_gitlab_attachments(gitlab_issue_or_comment)`
- Parse GitLab content for attachment URLs (format: `/uploads/...`)
- Download each file using GitLab API or direct HTTP requests
- Return list of downloaded files with metadata

#### `upload_github_attachments(github_repo, files)`
- Upload files to GitHub using PyGithub
- Return mapping of old URLs to new GitHub URLs
- Update content text to use new GitHub URLs

### 6. Cleanup and Validation

#### `cleanup_placeholders(github_repo)`
- Delete all placeholder issues (title = "Placeholder")
- Delete all placeholder milestones (title = "Placeholder Milestone")
- Final validation that remaining content matches GitLab

#### `validate_migration(gitlab_project, github_repo)`
- Verify issue counts match (excluding placeholders)
- Verify milestone counts match (excluding placeholders)
- Verify label translations applied correctly
- Check that cross-references work
- Generate migration report with statistics

## Script Architecture

```
gitlab_to_github_migrator.py
├── main()
│   ├── Parse CLI arguments with validation
│   ├── Initialize GitLab API client (python-gitlab)
│   ├── Initialize GitHub API client (PyGithub)
│   ├── Create GitHub repository
│   ├── Migrate repository content
│   ├── Handle labels
│   ├── Migrate milestones with number preservation
│   ├── Migrate issues with number preservation
│   ├── Cleanup placeholders
│   └── Validate migration and generate report
├── Helper functions for each major operation
├── Error handling and logging throughout
└── Configuration validation
```

## Error Handling Requirements

- **API Connectivity**: Validate GitLab and GitHub API access before starting
- **Repository Conflicts**: Check if GitHub repository already exists
- **Number Verification**: Verify each milestone/issue number assignment immediately after creation
- **Rate Limiting**: Handle API rate limits gracefully
- **Rollback Capability**: Provide option to delete created repository if critical errors occur
- **Comprehensive Logging**: Log all operations with timestamps and details
- **Attachment Handling**: Graceful handling of attachment download/upload failures

## Validation Requirements

- **Pre-migration**: Verify API access, check for conflicts
- **During migration**: Validate number assignments after each creation
- **Post-migration**: Comprehensive validation of all migrated content
- **Report Generation**: Summary of migrated items and any issues encountered

## Important Implementation Notes

1. **Number Preservation Strategy**: Use placeholder creation to ensure GitLab issue #X becomes GitHub issue #X
2. **Label Translation**: Support multiple translation patterns via repeated CLI arguments
3. **Cross-reference Preservation**: Since issue numbers are preserved, original cross-references work automatically (this handles parent/child relationships)
4. **Attachment Migration**: Download from GitLab, upload to GitHub, update references in text
5. **State Preservation**: Maintain open/closed state for both issues and milestones
6. **Author Attribution**: Since GitHub users may not match GitLab users, include author info in descriptions/comments

## Success Criteria

The completed script should:
- Accept CLI arguments as specified
- Preserve exact GitLab→GitHub issue and milestone numbering
- Migrate all content: titles, descriptions, comments, attachments
- Apply configurable label translations
- Preserve cross-references and parent/child relationships
- Handle errors gracefully with detailed logging
- Provide comprehensive validation and reporting