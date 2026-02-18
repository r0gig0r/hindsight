"""Iris parser implementation using the Vectorize Iris HTTP API."""

import asyncio
import logging
import mimetypes
import time
from pathlib import Path

import httpx

from .base import FileParser

logger = logging.getLogger(__name__)

_IRIS_BASE_URL = "https://api.vectorize.io/v1"
_DEFAULT_POLL_INTERVAL = 2.0  # seconds
_DEFAULT_TIMEOUT = 300.0  # seconds


class IrisParser(FileParser):
    """
    Iris file parser using the Vectorize Iris cloud extraction service.

    Uploads files to the Vectorize Iris API, starts an extraction job,
    and polls until the text is ready.

    Supported formats:
    - PDF (.pdf)
    - Word (.docx, .doc)
    - PowerPoint (.pptx, .ppt)
    - Excel (.xlsx, .xls)
    - Images (.jpg, .jpeg, .png, .gif, .bmp, .tiff, .webp)
    - HTML (.html, .htm)
    - Text (.txt, .md, .csv)

    Authentication:
        Requires VECTORIZE_TOKEN and VECTORIZE_ORG_ID environment variables,
        or pass them explicitly via the constructor.
    """

    def __init__(
        self,
        token: str,
        org_id: str,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        timeout: float = _DEFAULT_TIMEOUT,
    ):
        """
        Initialize iris parser.

        Args:
            token: Vectorize API token (VECTORIZE_TOKEN)
            org_id: Vectorize organization ID (VECTORIZE_ORG_ID)
            poll_interval: Seconds between status poll requests (default: 2)
            timeout: Maximum seconds to wait for extraction (default: 300)
        """
        self._token = token
        self._org_id = org_id
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._auth_headers = {"Authorization": f"Bearer {token}"}

    async def convert(self, file_data: bytes, filename: str) -> str:
        """Parse file to text using the Vectorize Iris API."""
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        async with httpx.AsyncClient() as client:
            # Step 1: Request a presigned upload URL
            init_resp = await client.post(
                f"{_IRIS_BASE_URL}/org/{self._org_id}/files",
                headers=self._auth_headers,
                json={"file_name": filename, "content_type": content_type},
            )
            init_resp.raise_for_status()
            init_data = init_resp.json()
            file_id: str = init_data["file_id"]
            upload_url: str = init_data["upload_url"]

            # Step 2: Upload the file bytes to the presigned URL (no auth header)
            upload_resp = await client.put(
                upload_url,
                content=file_data,
                headers={"Content-Type": content_type},
            )
            upload_resp.raise_for_status()

            # Step 3: Start extraction
            extract_resp = await client.post(
                f"{_IRIS_BASE_URL}/org/{self._org_id}/extraction",
                headers=self._auth_headers,
                json={"file_id": file_id},
            )
            extract_resp.raise_for_status()
            extraction_id: str = extract_resp.json()["extraction_id"]

            # Step 4: Poll until ready or timeout
            deadline = time.monotonic() + self._timeout
            while True:
                status_resp = await client.get(
                    f"{_IRIS_BASE_URL}/org/{self._org_id}/extraction/{extraction_id}",
                    headers=self._auth_headers,
                )
                status_resp.raise_for_status()
                status_data = status_resp.json()

                if status_data.get("ready"):
                    data = status_data.get("data", {})
                    if not data.get("success"):
                        error = data.get("error", "unknown error")
                        raise RuntimeError(f"Iris extraction failed for '{filename}': {error}")
                    text = data.get("text")
                    if not text:
                        raise RuntimeError(f"No content extracted from '{filename}'")
                    return text

                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Iris extraction timed out after {self._timeout}s for '{filename}'")

                await asyncio.sleep(self._poll_interval)

    def supports(self, filename: str, content_type: str | None = None) -> bool:
        """Check if iris supports this file type."""
        supported_extensions = {
            # Documents
            ".pdf",
            ".docx",
            ".doc",
            ".pptx",
            ".ppt",
            ".xlsx",
            ".xls",
            # Images
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".tiff",
            ".webp",
            # Web
            ".html",
            ".htm",
            # Text
            ".txt",
            ".md",
            ".csv",
        }
        ext = Path(filename).suffix.lower()
        return ext in supported_extensions

    def name(self) -> str:
        """Get parser name."""
        return "iris"
