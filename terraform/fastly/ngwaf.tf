# Next-Gen WAF / Bot Management — the actual bot-killer.
#
# This serves an *invisible* (non-interactive, JavaScript proof-of-work) browser
# challenge on the two unauthenticated form POSTs. Real browsers solve it without
# a visible CAPTCHA; headless/scripted bots can't, so they never reach the origin.
#
# Requires the Next-Gen WAF + Bot Management entitlement, the NGWAF agent/edge
# deployment in front of the service, and SIGSCI_EMAIL / SIGSCI_TOKEN set. If you
# haven't enabled NGWAF yet, the edge rate limiting in main.tf still stands on its
# own — you can leave this file out (or unset ngwaf_site_short_name) until then.

resource "sigsci_site_rule" "challenge_form_posts" {
  count = var.ngwaf_site_short_name == "" ? 0 : 1

  site_short_name = var.ngwaf_site_short_name
  type            = "request"
  group_operator  = "all"
  enabled         = true
  reason          = "Bot challenge on unauthenticated RUYFO event/recovery form POSTs"
  expiration      = "" # never expires

  # method == POST  AND  (path == /events OR path == /recover)
  conditions {
    type     = "single"
    field    = "method"
    operator = "equals"
    value    = "POST"
  }

  conditions {
    type           = "group"
    group_operator = "any"

    conditions {
      type     = "single"
      field    = "path"
      operator = "equals"
      value    = "/events"
    }

    conditions {
      type     = "single"
      field    = "path"
      operator = "equals"
      value    = "/recover"
    }
  }

  actions {
    type = "browserChallenge"
    # false = non-interactive JS proof-of-work (invisible to humans).
    # Flip to true if you'd rather present an interactive challenge.
    allow_interactive = false
  }
}
