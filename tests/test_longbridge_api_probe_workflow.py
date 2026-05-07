import unittest
from pathlib import Path


class LongBridgeApiProbeWorkflowTests(unittest.TestCase):
    def test_workflow_uses_hk_environment_and_gcp_secrets(self) -> None:
        workflow = Path(".github/workflows/longbridge-api-probe.yml").read_text(encoding="utf-8")

        self.assertIn("name: LongBridge API Probe", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("environment: longbridge-hk", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("repository: QuantStrategyLab/QuantPlatformKit", workflow)
        self.assertIn("ref: ${{ inputs.qpk_ref }}", workflow)
        self.assertIn("google-github-actions/auth@v3", workflow)
        self.assertIn("google-github-actions/setup-gcloud@v3", workflow)
        self.assertIn("CLOUD_RUN_REGION: ${{ vars.CLOUD_RUN_REGION }}", workflow)
        self.assertIn("CLOUD_RUN_SERVICE: ${{ vars.CLOUD_RUN_SERVICE }}", workflow)
        self.assertIn("LONGPORT_APP_KEY_SECRET_NAME: ${{ vars.LONGPORT_APP_KEY_SECRET_NAME }}", workflow)
        self.assertIn("LONGPORT_APP_SECRET_SECRET_NAME: ${{ vars.LONGPORT_APP_SECRET_SECRET_NAME }}", workflow)
        self.assertIn("LONGPORT_SECRET_NAME: ${{ vars.LONGPORT_SECRET_NAME }}", workflow)
        self.assertIn("LONGPORT_APP_KEY_VALUE: ${{ secrets.LONGPORT_APP_KEY }}", workflow)
        self.assertIn("LONGPORT_APP_SECRET_VALUE: ${{ secrets.LONGPORT_APP_SECRET }}", workflow)
        self.assertIn("LONGPORT_ACCESS_TOKEN_VALUE: ${{ secrets.LONGPORT_ACCESS_TOKEN }}", workflow)
        self.assertIn("read_secret_manager_value()", workflow)
        self.assertIn('read_secret_manager_value "${LONGPORT_APP_KEY_SECRET_NAME}"', workflow)
        self.assertIn('read_secret_manager_value "${LONGPORT_APP_SECRET_SECRET_NAME}"', workflow)
        self.assertIn('read_secret_manager_value "${LONGPORT_SECRET_NAME}"', workflow)
        self.assertIn('gcloud secrets versions access latest --secret="${secret_name}"', workflow)
        self.assertIn("--impersonate-service-account", workflow)
        self.assertIn("python -m pip install -e quant-platform-kit pytest longport", workflow)
        self.assertIn('LONGBRIDGE_API_PROBE: "1"', workflow)
        self.assertIn("test_longbridge_fractional_order_api_probe.py", workflow)


if __name__ == "__main__":
    unittest.main()
