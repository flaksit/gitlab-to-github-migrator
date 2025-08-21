# Research Notes - Owner for Cleanup Feature

## Requirements Summary

Update `delete_test_repos.py` to:
- Add required `github_owner` command line argument
- Replace hardcoded `"abuflow"` organization with parameterized owner
- Support both organization and user owners (like `create_repo()` in `github_utils.py`)

## Current Implementation Analysis

### delete_test_repos.py Current State
- **Hardcoded owner**: Line 64 uses `github_client.get_organization("abuflow")`
- **Argument structure**: Currently takes optional positional `pass_path` argument
- **TODOs**: Lines 14-15 mention adding required command line arg and making pass_path a flag
- **Function flow**: Gets token → gets organization → lists repos → filters test repos → deletes them

### Existing Pattern in github_utils.py
The `create_repo()` function (lines 43-74) shows the standard pattern for handling owner:
```python
try:
    org: Organization = client.get_organization(owner)  # Try organization first
    return org.create_repo(...)
except UnknownObjectException as e:
    if e.status == 404:
        # Fall back to authenticated user
        authenticated_user = client.get_user()
        if owner != authenticated_user.login:
            raise MigrationError(...)  # Validate user match
        return authenticated_user.create_repo(...)
```

## CLI Design Research

### Best Practices from Web Research

**Positional Arguments** (recommended for `github_owner`):
- Should be used for essential data that the program absolutely needs
- Core functionality data that your program must have to operate
- User expects them to be mandatory and provided in correct order

**Optional Flags** (recommended for `pass_path`):
- Should be used for configuration options and non-essential functionality
- Required options are considered bad form - users expect options to be optional
- Provide flexibility without requiring every parameter every time

**GitHub CLI Patterns**:
- Uses `OWNER/REPO` format but we only need `OWNER` since we're scanning multiple repos
- Context awareness is valued but not applicable here (we're specifying the owner explicitly)
- Standard Unix-style flags for optional parameters

## Implementation Strategy

### Argument Design Decision
- `github_owner` → **Required positional argument** (essential data)
- `pass_path` → **Optional flag** `--token-path` or `--pass-path` (configuration)

### Owner Handling Strategy
Reuse the proven pattern from `github_utils.py`:
1. Try to get as Organization first
2. If 404, fall back to authenticated user
3. Validate user match if using user account
4. Use appropriate API methods for each type

### Repository Scanning Strategy
- Organizations: Use `org.get_repos()` (current approach)
- Users: Use `user.get_repos()` for authenticated user's repositories

## Key Considerations

### Error Handling
- Handle case where owner doesn't exist (neither org nor user)
- Handle case where user doesn't match authenticated user
- Preserve existing error handling for token and deletion operations

### Backwards Compatibility
- This is a breaking change (adds required argument)
- Update help text and examples
- Consider if version bump needed

### Testing Considerations
- Test with organization owner
- Test with user owner (matching authenticated user)  
- Test with invalid owner
- Test with user owner that doesn't match authenticated user

### Security Implications
- No security concerns - same permissions model as before
- Still requires token with appropriate delete permissions
- Owner parameter doesn't introduce new attack vectors