import os

# The hosted default (HMRC sandbox) hides hmrc_check_mtd_status. The suite
# exercises the full tool surface a production-configured instance exposes;
# tests/test_hosted_surface.py checks the hosted default in a fresh process.
# Set before any test module imports src.gateway, which reads it at import.
os.environ["HMRC_API_BASE"] = "https://api.service.hmrc.gov.uk"
os.environ.pop("MCPOBS_IP_LABEL", None)
