import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import pipeline, youtube_upload  # noqa: E402
from src.config import get_slot  # noqa: E402


class RequestBodyTests(unittest.TestCase):
    def test_publish_at_forces_private_status(self):
        # スケジュール公開は privacyStatus=private + publishAt でしか成立しないため、
        # publishAt を渡したときに public のまま送ってしまわないこと。
        body = youtube_upload.build_request_body(
            title="t", description="d", tags=["a"], category_id="27",
            privacy_status="public", publish_at_iso="2026-08-07T00:00:00Z",
        )
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertEqual(body["status"]["publishAt"], "2026-08-07T00:00:00Z")

    def test_without_publish_at_keeps_requested_status(self):
        body = youtube_upload.build_request_body(
            title="t", description="d", tags=["a"], category_id="27",
            privacy_status="public", publish_at_iso=None,
        )
        self.assertEqual(body["status"]["privacyStatus"], "public")
        self.assertNotIn("publishAt", body["status"])

    def test_title_is_truncated_to_youtube_limit(self):
        body = youtube_upload.build_request_body(
            title="あ" * 250, description="d", tags=[], category_id="27",
            privacy_status="private", publish_at_iso=None,
        )
        self.assertEqual(len(body["snippet"]["title"]), 100)

    def test_missing_credentials_raises(self):
        with self.assertRaises(youtube_upload.MissingCredentialsError):
            youtube_upload._env_creds("nonexistent_market_xyz")


class ScheduleTests(unittest.TestCase):
    def test_publish_at_is_in_the_future_and_utc(self):
        for market, slot_id in [("ja", "kokoro"), ("ja", "okane"), ("en", "daily"), ("zh", "daily")]:
            iso = pipeline.compute_publish_at(get_slot(market, slot_id))
            self.assertTrue(iso.endswith("Z"), iso)
            parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            self.assertGreater(parsed, datetime.now(timezone.utc), f"{market}/{slot_id} -> {iso}")


if __name__ == "__main__":
    unittest.main()
