import unittest
from unittest.mock import patch

from bot import database


class SubscriptionLinkTests(unittest.TestCase):
    def test_raw_and_json_use_same_subscription_id(self):
        with patch.multiple(
            database,
            XUI_PUBLIC_SUBSCRIPTION_BASE_URL="https://sub.example.test:2096/",
            XUI_SUBSCRIPTION_LINK_TEMPLATE="{base}/sub/{subId}",
            XUI_JSON_SUBSCRIPTION_LINK_TEMPLATE="{base}/json/{subId}",
        ):
            record = {"subId": "example/id"}
            self.assertEqual(database.build_subscription_link(record), "https://sub.example.test:2096/sub/example%2Fid")
            self.assertEqual(database.build_json_subscription_link(record), "https://sub.example.test:2096/json/example%2Fid")

    def test_panel_ready_url_can_supply_subscription_port_and_id(self):
        with patch.multiple(
            database,
            XUI_PUBLIC_SUBSCRIPTION_BASE_URL="",
            XUI_BASE_URL="https://panel.example.test/private",
            XUI_JSON_SUBSCRIPTION_LINK_TEMPLATE="{base}/json/{subId}",
        ):
            record = {"subscriptionLink": "https://sub.example.test:2096/sub/test-id?html=1"}
            self.assertEqual(database.build_subscription_link(record), record["subscriptionLink"])
            self.assertEqual(database.build_json_subscription_link(record), "https://sub.example.test:2096/json/test-id")

    def test_json_custom_path_and_empty_or_invalid_template(self):
        with patch.multiple(
            database,
            XUI_PUBLIC_SUBSCRIPTION_BASE_URL="https://sub.example.test/prefix",
            XUI_JSON_SUBSCRIPTION_LINK_TEMPLATE="{base}/custom-xray/{subId}?email={email}",
        ):
            record = {"sub_id": "test-id", "email": "test@example.test"}
            self.assertEqual(
                database.build_json_subscription_link(record),
                "https://sub.example.test/prefix/custom-xray/test-id?email=test%40example.test",
            )
            self.assertIsNone(database.build_json_subscription_link({"email": "test@example.test"}))
        for template in ("", "{base}/{missing}", "/relative/{subId}"):
            with self.subTest(template=template), patch.object(database, "XUI_JSON_SUBSCRIPTION_LINK_TEMPLATE", template):
                self.assertIsNone(database.build_json_subscription_link(record))
