# SourceSystem Protocol Design

This document describes the architecture for generalizing the migrator to support
multiple source systems (GitLab, Azure DevOps) migrating to GitHub.  
See GitHub issue #76.

## Overview

The migration architecture separates concerns into three components:

| Component | Responsibility | Examples |
|-----------|---------------|----------|
| **SourceSystem** | Extract data, transform content | GitLabSource, AzureDevOpsSource |
| **TargetSystem** | Create resources, upload attachments | GitHubTarget |
| **Migrator** | Orchestrate flow, manage number mapping | Single implementation |

## Key Files

- `src/gitlab_to_github_migrator/models.py` - Data classes for normalized migration data
- `src/gitlab_to_github_migrator/protocols.py` - SourceSystem and TargetSystem protocols
- `src/gitlab_to_github_migrator/orchestrator.py` - Migrator class that coordinates the flow

## Migration Flow

```mermaid
sequenceDiagram
    participant M as Migrator
    participant S as SourceSystem
    participant T as TargetSystem

    Note over M,T: Phase 1: Preparation
    M->>T: validate_access()
    M->>T: create_repository()
    M->>S: (get git clone URL)
    M->>T: push_git_content()
    M->>S: get_issue_numbers()
    Note over M: Build number_map<br/>(source→target)

    Note over M,T: Phase 2: Labels
    M->>S: get_labels()
    loop Each label
        M->>T: create_label()
    end

    Note over M,T: Phase 3: Milestones (optional)
    M->>S: get_milestones()
    loop Each milestone
        M->>T: create_milestone()
    end
    Note over M: Build milestone_map

    Note over M,T: Phase 4: Issues + Comments
    loop Each issue (in migration order)
        M->>S: get_issue(source_number)
        M->>S: get_comments(source_number)

        Note over M,S,T: Transform issue body
        M->>S: extract_attachments(body)
        loop Each attachment
            M->>T: upload_attachment()
        end
        Note over M: Build attachment_url_map
        M->>S: transform_content(body, number_map, url_map)

        M->>T: create_issue(transformed_issue)

        loop Each comment
            Note over M,S,T: Transform comment (same as body)
            M->>T: create_comment()
        end
    end

    Note over M,T: Phase 5: Relationships
    M->>S: get_relationships()
    Note over M: Translate source→target numbers
    loop Each relationship
        alt parent-child
            M->>T: create_parent_child()
        else blocks/blocked_by
            M->>T: create_dependency()
        end
    end
```

## Number Mapping Strategy

Different source systems have different numbering schemes:

| Source | Numbering | Example |
|--------|-----------|---------|
| GitLab | Sequential from 1, may have gaps | 1, 2, 5, 6 (3, 4 deleted) |
| Azure DevOps | High numbers, sparse | 7001, 7005, 7008 |
| GitHub (target) | Sequential from 1 | 1, 2, 3, ... |

The Migrator pre-computes the number_map before creating any issues:

```python
source_numbers = source.get_issue_numbers()  # [7001, 7005, 7008]
number_map = {src: i+1 for i, src in enumerate(source_numbers)}
# {7001: 1, 7005: 2, 7008: 3}
```

This enables single-pass content transformation - issue references like `#7005` can
be replaced with `#2` immediately, without needing a second pass after all issues exist.

## Attachment Flow

Attachments require coordination between source and target because:
1. Source knows its own URL patterns (how to find and extract them)
2. Target determines the new URLs (after upload)
3. Source knows how to replace its patterns

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Original body  │     │  Attachments    │     │  Transformed    │
│  with source    │────▶│  uploaded to    │────▶│  body with      │
│  URLs           │     │  target         │     │  target URLs    │
└─────────────────┘     └─────────────────┘     └─────────────────┘
        │                       │                       ▲
        │                       │                       │
        ▼                       ▼                       │
   Source.extract_        Target.upload_         Source.transform_
   attachments()          attachment()           content()
```

This is the only unavoidable round-trip between source and target per content block.

## Source-Specific Considerations

### GitLab

- Issue references: `#123` pattern
- Attachment URLs: `/uploads/{secret}/{filename}` pattern
- Has milestones with numbers
- Relationships: parent-child (via work items), blocks/blocked_by/related

### Azure DevOps

- Issue references: `#7001` or `AB#7001` patterns
- Attachment URLs: Azure DevOps-specific API URLs
- No milestones (uses iterations/sprints instead, not migrated)
- HTML content requires conversion to Markdown
- Relationships: parent-child, related, predecessor/successor

## Design Decisions

### Why separate protocols instead of inheritance?

Protocols (structural typing) allow:
- Independent implementations without base class coupling
- Easy mocking for tests
- Clear contract documentation

### Why pre-compute number_map?

Alternatives considered:
1. **Two-pass**: Create all issues, then update bodies with correct references
   - Rejected: Doubles API calls, more complex error handling
2. **Leave forward references unresolved**: Only resolve refs to already-created issues
   - Rejected: Incomplete migration, confusing for users
3. **Pre-compute** (chosen): Get issue list first, compute mapping, single-pass creation
   - Benefit: Simple, complete, efficient

### Why does Source handle transform_content?

Each source has unique URL and reference patterns. Keeping this knowledge in the
source implementation:
- Encapsulates source-specific regex/parsing
- Allows different transformation strategies per source
- Keeps Migrator simple and source-agnostic
