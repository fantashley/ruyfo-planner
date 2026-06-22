terraform {
  required_version = ">= 1.5"

  required_providers {
    # Delivery (VCL) service: domains, backend, edge rate limiting.
    fastly = {
      source  = "fastly/fastly"
      version = ">= 5.0"
    }

    # Next-Gen WAF / Bot Management: the browser challenge in ngwaf.tf.
    # Only needed if you enable that file (requires the NGWAF + Bot Management
    # entitlement on your account).
    sigsci = {
      source  = "signalsciences/sigsci"
      version = ">= 3.0"
    }
  }
}

# Fastly auth via environment (FASTLY_API_KEY), so no token lands in these files.
provider "fastly" {}

# Next-Gen WAF creds. The sigsci provider marks these required, so pass them as
# variables (set via TF_VAR_ngwaf_email / TF_VAR_ngwaf_auth_token to keep them
# out of files). Only actually used when ngwaf.tf creates a rule.
provider "sigsci" {
  corp       = var.ngwaf_corp
  email      = var.ngwaf_email
  auth_token = var.ngwaf_auth_token
}
