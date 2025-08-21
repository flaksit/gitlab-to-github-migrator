# Implementation Plan - Owner for Cleanup Feature

## Task Breakdown

### Phase 1: Argument Parsing Updates

#### Task 1.1: Update argument parser configuration
- **File:** `delete_test_repos.py:119-133` (main function)
- **Changes:**
  - Add required positional argument `github_owner` as first argument
  - Change `pass_path` from optional (`nargs="?"`) to required positional argument
  - Update help text and examples in epilog
- **Validation:** Help output shows correct argument structure
- **Estimate:** 15 minutes

#### Task 1.2: Update function signatures
- **File:** `delete_test_repos.py:52` (delete_test_repositories function)
- **Changes:**
  - Add `owner_name: str` as first parameter
  - Keep `pass_path: str | None = None` as second parameter
- **File:** `delete_test_repos.py:135` (main function call)
- **Changes:**
  - Pass `args.github_owner` to delete_test_repositories
  - Pass `args.pass_path` as second argument
- **Validation:** Function signatures match new calling pattern
- **Estimate:** 10 minutes

### Phase 2: Owner Detection Logic

#### Task 2.1: Create get_owner_repos function
- **File:** `delete_test_repos.py` (new function after imports)
- **Changes:**
  - Create function with signature: `get_owner_repos(client: Github, owner_name: str) -> tuple[str, list[Repository]]`
  - Implement try-organization-first pattern from github_utils.py
  - Handle 404 fallback to authenticated user
  - Validate user matches authenticated user for user accounts
  - Return tuple of (owner_type, repos_list) where owner_type is "organization" or "user"
- **Error Handling:**
  - Raise `MigrationError` for invalid owners
  - Raise `MigrationError` for user mismatch
- **Validation:** Function correctly identifies org vs user and returns appropriate repos
- **Estimate:** 45 minutes

#### Task 2.2: Add required imports
- **File:** `delete_test_repos.py:1-26` (imports section)
- **Changes:**
  - Add: `from github.AuthenticatedUser import AuthenticatedUser`
  - Add: `from github.Organization import Organization`
  - Add: `from github.Repository import Repository`
  - Add: `from .exceptions import MigrationError`
- **Validation:** Imports resolve correctly
- **Estimate:** 5 minutes

### Phase 3: Main Logic Integration

#### Task 3.1: Replace hardcoded organization logic
- **File:** `delete_test_repos.py:62-71` (repository scanning)
- **Changes:**
  - Replace hardcoded `org = github_client.get_organization("abuflow")` with call to `get_owner_repos()`
  - Update logging messages to use dynamic owner name and type
  - Use returned repos list instead of `org.get_repos()`
- **Before:**
  ```python
  org = github_client.get_organization("abuflow")
  print(f"🔍 Scanning repositories in {org.login} organization...")
  repos = list(org.get_repos())
  ```
- **After:**
  ```python
  owner_type, repos = get_owner_repos(github_client, owner_name)
  print(f"🔍 Scanning repositories for {owner_type} '{owner_name}'...")
  ```
- **Validation:** Script works with both organization and user owners
- **Estimate:** 20 minutes

### Phase 4: Error Handling Enhancement

#### Task 4.1: Add owner-specific error handling
- **File:** `delete_test_repos.py:52-112` (delete_test_repositories function)
- **Changes:**
  - Wrap `get_owner_repos()` call in try-except block
  - Catch `MigrationError` and convert to user-friendly error messages
  - Exit with status code 1 for owner-related errors
- **Example:**
  ```python
  try:
      owner_type, repos = get_owner_repos(github_client, owner_name)
  except MigrationError as e:
      print(f"❌ Error: {e}")
      sys.exit(1)
  ```
- **Validation:** Clear error messages for invalid owners
- **Estimate:** 15 minutes

### Phase 5: Documentation Updates

#### Task 5.1: Update docstring and help text
- **File:** `delete_test_repos.py:1-16` (module docstring)
- **Changes:**
  - Update description to mention configurable owner instead of hardcoded "abuflow"
  - Update usage example with new argument structure
  - Update args documentation
- **File:** `delete_test_repos.py:122-127` (argparse epilog)
- **Changes:**
  - Update examples to show new argument structure
  - Add examples for both organization and user
- **Example:**
  ```
  Examples:
    uv run delete_test_repos abuflow github/api/token           # Organization
    uv run delete_test_repos myusername github/api/token        # User  
    uv run delete_test_repos abuflow github/admin/token         # With admin token
  ```
- **Validation:** Help text is clear and accurate
- **Estimate:** 10 minutes

### Phase 6: Testing and Validation

#### Task 6.1: Manual testing scenarios
- **Test Cases:**
  1. Run with organization owner (existing functionality)
  2. Run with user owner (new functionality)
  3. Run with invalid owner (error handling)
  4. Run with mismatched user (security validation)
  5. Test new argument parsing (two required positional arguments)
- **Validation Method:** Manual execution with test scenarios
- **Estimate:** 30 minutes

#### Task 6.2: Code review self-check
- **Review Areas:**
  - Type annotations are correct
  - Error messages are user-friendly
  - Logging messages are consistent  
  - No hardcoded values remain
  - Import statements are minimal and correct
- **Validation:** Code follows project conventions
- **Estimate:** 15 minutes

## Implementation Order

1. **Phase 1 (Tasks 1.1-1.2)**: Argument parsing - establishes new interface
2. **Phase 2 (Tasks 2.1-2.2)**: Owner detection - core new functionality  
3. **Phase 3 (Task 3.1)**: Integration - connects new logic to existing workflow
4. **Phase 4 (Task 4.1)**: Error handling - robustness
5. **Phase 5 (Task 5.1)**: Documentation - user experience
6. **Phase 6 (Tasks 6.1-6.2)**: Testing - validation

## Risk Mitigation

**Breaking Changes:**
- This change is inherently breaking due to required positional argument
- Minimize additional breaking changes by preserving all other behavior
- Clear documentation of the change in commit message

**Testing Strategy:**
- Test against both organization and user accounts before finalizing
- Verify error cases don't crash but provide helpful messages
- Confirm existing deletion logic remains unchanged

## Success Criteria

- ✅ Script accepts `github_owner` as first required positional argument
- ✅ Script accepts `pass_path` as second required positional argument
- ✅ Script works with organization owners (preserves existing functionality)  
- ✅ Script works with user owners (new functionality)
- ✅ Script provides clear error messages for invalid owners
- ✅ Script prevents operating on mismatched user accounts
- ✅ All existing deletion logic and safety measures remain intact
- ✅ Help text accurately reflects new usage

## Total Estimated Time
**2 hours 45 minutes** across all tasks

This accounts for implementation, testing, and documentation updates needed to complete the feature safely and thoroughly.