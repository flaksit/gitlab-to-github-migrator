"""Attachment migration between GitLab and GitHub."""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from github import GithubException

from . import gitlab_utils as glu

if TYPE_CHECKING:
    import github.GitRelease
    import github.Repository
    from gitlab import Gitlab
    from gitlab.v4.objects import Project as GitlabProject

logger: logging.Logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DownloadedFile:
    """Represents a downloaded file from GitLab."""

    filename: str
    content: bytes
    short_gitlab_url: str
    full_gitlab_url: str


@dataclass
class ProcessedContent:
    """Result of processing content with attachments."""

    content: str
    attachment_count: int


@dataclass
class DownloadResult:
    """Result of downloading files from GitLab."""

    files: list[DownloadedFile]
    updated_content: str
    attachment_count: int


class AttachmentHandler:
    """Downloads attachments from GitLab and uploads to GitHub releases."""

    _gitlab_client: Gitlab
    _gitlab_project: GitlabProject
    _github_repo: github.Repository.Repository
    _uploaded_cache: dict[str, str]
    _release: github.GitRelease.GitRelease | None

    def __init__(
        self,
        gitlab_client: Gitlab,
        gitlab_project: GitlabProject,
        github_repo: github.Repository.Repository,
    ) -> None:
        self._gitlab_client = gitlab_client
        self._gitlab_project = gitlab_project
        self._github_repo = github_repo
        self._uploaded_cache = {}
        self._release = None

    @property
    def attachments_release(self) -> github.GitRelease.GitRelease:
        """Get or create the draft release for storing attachments (cached)."""
        if self._release is None:
            release_tag = "gitlab-issue-attachments"
            release_name = "GitLab issue attachments"

            # Find existing release by name (draft releases can't be found by tag)
            for r in self._github_repo.get_releases():
                if r.name == release_name:
                    self._release = r
                    logger.debug(f"Using existing attachments release: {r.name}")
                    return self._release

            # Create new release
            print(f"Creating new '{release_name}' release for attachments")
            self._release = self._github_repo.create_git_release(
                tag=release_tag,
                name=release_name,
                message="Storage for migrated GitLab attachments. Do not delete.",
                draft=True,
            )

        return self._release

    def process_content(self, content: str, context: str = "") -> ProcessedContent:
        """Download GitLab attachments and upload to GitHub, returning updated content.

        Args:
            content: Text content that may contain GitLab attachment URLs
            context: Context for log messages (e.g., "issue #5")

        Returns:
            ProcessedContent with updated content and attachment count
        """
        download_result = self._download_files(content)
        final_content = self._upload_files(download_result.files, download_result.updated_content, context)
        return ProcessedContent(content=final_content, attachment_count=download_result.attachment_count)

    def _download_files(self, content: str) -> DownloadResult:
        """Find attachment URLs, download files, replace cached URLs.

        Returns:
            DownloadResult with files to upload, updated content, and attachment count
        """
        attachment_pattern = r"/uploads/([a-f0-9]{32})/([^)\s]+)"
        attachments = re.findall(attachment_pattern, content)

        downloaded_files: list[DownloadedFile] = []
        updated_content = content

        for secret, filename in attachments:
            short_url = f"/uploads/{secret}/{filename}"

            # If already uploaded, just replace URL
            if short_url in self._uploaded_cache:
                github_url = self._uploaded_cache[short_url]
                updated_content = updated_content.replace(short_url, github_url)
                logger.debug(f"Reusing cached attachment {filename}: {github_url}")
                continue

            full_url = f"{self._gitlab_project.web_url}{short_url}"
            try:
                attachment_content, content_type = glu.download_attachment(
                    self._gitlab_client,
                    self._gitlab_project,  # pyright: ignore[reportUnknownArgumentType]
                    secret,
                    filename,
                )

                if attachment_content:
                    downloaded_files.append(
                        DownloadedFile(
                            filename=filename,
                            content=attachment_content,
                            short_gitlab_url=short_url,
                            full_gitlab_url=full_url,
                        )
                    )
                else:
                    logger.warning(
                        f"GitLab returned empty content for attachment {short_url} (Content-Type: {content_type})"
                    )

            except Exception as e:
                logger.warning(f"Failed to download attachment {short_url}: {e}")

        return DownloadResult(
            files=downloaded_files, updated_content=updated_content, attachment_count=len(attachments)
        )

    def _upload_files(self, files: list[DownloadedFile], content: str, context: str) -> str:
        """Upload files to GitHub release, update content with new URLs."""
        if not files:
            return content

        updated_content = content
        release = self.attachments_release

        for file_info in files:
            # Skip if already cached
            if file_info.short_gitlab_url in self._uploaded_cache:
                url = self._uploaded_cache[file_info.short_gitlab_url]
                updated_content = updated_content.replace(file_info.short_gitlab_url, url)
                continue

            # Skip empty files
            if not file_info.content:
                ctx = f" in {context}" if context else ""
                logger.warning(f"Skipping empty attachment {file_info.filename}{ctx}")
                continue

            temp_path = None
            try:
                file_ext = Path(file_info.filename).suffix if file_info.filename else ""
                with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as f:
                    temp_path = f.name
                    f.write(file_info.content)

                # Make filename unique with secret prefix
                url_parts = file_info.short_gitlab_url.split("/")
                secret = url_parts[2] if len(url_parts) >= 3 else ""
                unique_name = f"{secret[:8]}_{file_info.filename}" if secret else file_info.filename

                asset = release.upload_asset(path=temp_path, name=unique_name)
                download_url = asset.browser_download_url

                self._uploaded_cache[file_info.short_gitlab_url] = download_url
                updated_content = updated_content.replace(file_info.short_gitlab_url, download_url)
                logger.debug(f"Uploaded {file_info.filename}: {download_url}")

            except (GithubException, OSError):
                logger.exception(f"Failed to upload attachment {file_info.filename}")
                raise
            finally:
                if temp_path:
                    p = Path(temp_path)
                    if p.exists():
                        p.unlink()

        return updated_content
