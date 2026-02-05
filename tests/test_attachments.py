"""Tests for attachment handling."""

from unittest.mock import Mock, patch

import pytest

from gitlab_to_github_migrator.attachments import AttachmentHandler, DownloadedFile


@pytest.mark.unit
class TestDownloadedFile:
    def test_creation(self) -> None:
        f = DownloadedFile(
            filename="test.png",
            content=b"image data",
            short_gitlab_url="/uploads/abc123/test.png",
            full_gitlab_url="https://gitlab.com/org/proj/uploads/abc123/test.png",
        )
        assert f.filename == "test.png"
        assert f.content == b"image data"


@pytest.mark.unit
class TestAttachmentHandler:
    def setup_method(self) -> None:
        self.mock_gitlab_client: Mock = Mock()
        self.mock_gitlab_project: Mock = Mock()
        self.mock_gitlab_project.id = 12345
        self.mock_gitlab_project.web_url = "https://gitlab.com/org/project"
        self.mock_github_repo: Mock = Mock()

    def test_process_content_no_attachments(self) -> None:
        handler = AttachmentHandler(
            self.mock_gitlab_client,
            self.mock_gitlab_project,
            self.mock_github_repo,
        )

        content = "No attachments here"
        result = handler.process_content(content)

        assert result == content

    def test_process_content_with_cached_attachment(self) -> None:
        handler = AttachmentHandler(
            self.mock_gitlab_client,
            self.mock_gitlab_project,
            self.mock_github_repo,
        )

        # Pre-populate cache
        handler._uploaded_cache["/uploads/abcdef0123456789abcdef0123456789/cached.pdf"] = (
            "https://github.com/releases/cached.pdf"
        )

        content = "See attachment: /uploads/abcdef0123456789abcdef0123456789/cached.pdf"
        result = handler.process_content(content)

        assert "/uploads/abcdef0123456789abcdef0123456789/cached.pdf" not in result
        assert "https://github.com/releases/cached.pdf" in result

    @patch("gitlab_to_github_migrator.attachments.glu.download_attachment")
    def test_process_content_downloads_and_uploads(self, mock_download) -> None:
        # Setup download mock
        mock_download.return_value = (b"file content", "application/pdf")

        # Setup upload mock (release)
        mock_release = Mock()
        mock_asset = Mock()
        mock_asset.browser_download_url = "https://github.com/releases/download/file.pdf"
        mock_release.upload_asset.return_value = mock_asset
        self.mock_github_repo.get_releases.return_value = [mock_release]
        mock_release.name = "GitLab issue attachments"

        handler = AttachmentHandler(
            self.mock_gitlab_client,
            self.mock_gitlab_project,
            self.mock_github_repo,
        )

        content = "File: /uploads/abcdef0123456789abcdef0123456789/doc.pdf"
        result = handler.process_content(content, context="issue #1")

        assert "/uploads/abcdef0123456789abcdef0123456789/doc.pdf" not in result
        assert "https://github.com/releases/download/file.pdf" in result

    @patch("gitlab_to_github_migrator.attachments.glu.download_attachment")
    def test_attachment_counters(self, mock_download) -> None:
        """Test that attachment counters track uploaded files and total references correctly."""
        # Setup download mock
        mock_download.return_value = (b"file content", "application/pdf")

        # Setup upload mock (release)
        mock_release = Mock()
        mock_asset = Mock()
        mock_asset.browser_download_url = "https://github.com/releases/download/file.pdf"
        mock_release.upload_asset.return_value = mock_asset
        self.mock_github_repo.get_releases.return_value = [mock_release]
        mock_release.name = "GitLab issue attachments"

        handler = AttachmentHandler(
            self.mock_gitlab_client,
            self.mock_gitlab_project,
            self.mock_github_repo,
        )

        # Initially counters should be zero
        assert handler.uploaded_files_count == 0
        assert handler.total_attachments_referenced == 0

        # Process content with one unique attachment
        content1 = "File: /uploads/abcdef0123456789abcdef0123456789/doc.pdf"
        handler.process_content(content1, context="issue #1")

        # Should have 1 upload and 1 reference
        assert handler.uploaded_files_count == 1
        assert handler.total_attachments_referenced == 1

        # Process content with the same attachment (cached)
        content2 = "Same file: /uploads/abcdef0123456789abcdef0123456789/doc.pdf"
        handler.process_content(content2, context="issue #2")

        # Should still have 1 upload (cached) but 2 references total
        assert handler.uploaded_files_count == 1
        assert handler.total_attachments_referenced == 2

        # Process content with multiple references including a new file
        mock_download.return_value = (b"image data", "image/png")
        content3 = (
            "Two files: /uploads/abcdef0123456789abcdef0123456789/doc.pdf "
            "and /uploads/fedcba9876543210fedcba9876543210/image.png"
        )
        handler.process_content(content3, context="issue #3")

        # Should have 2 uploads (one new) and 4 references total (2 from content3 + 2 from before)
        assert handler.uploaded_files_count == 2
        assert handler.total_attachments_referenced == 4
