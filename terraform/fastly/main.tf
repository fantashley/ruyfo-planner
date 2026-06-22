resource "fastly_service_vcl" "ruyfo" {
  name = var.service_name

  domain {
    name = var.domain_name
  }

  backend {
    name             = "origin"
    address          = var.backend_address
    port             = var.backend_port
    use_ssl          = var.backend_use_ssl
    ssl_sni_hostname = var.backend_use_ssl ? var.domain_name : null
    override_host    = var.domain_name
  }

  # --- Origin lock-down: stamp the shared secret on every origin request -----
  # The app verifies X-Origin-Secret and rejects anything missing it, so a
  # leaked origin IP can't be hit directly to bypass these edge rules. No-op
  # when origin_shared_secret is "" (e.g. if you firewall to Fastly IPs instead).
  dynamic "header" {
    for_each = var.origin_shared_secret == "" ? [] : [1]
    content {
      name        = "Stamp origin verification secret"
      type        = "request"
      action      = "set"
      destination = "http.X-Origin-Secret"
      source      = "\"${var.origin_shared_secret}\""
      priority    = 10
    }
  }

  # --- Edge rate limiting on the unauthenticated form POSTs ------------------
  # /events (create) and /recover (link recovery) are the two endpoints that an
  # anonymous client can use to make the app send mail. Cap per-client volume so
  # a bot can't hammer them; the per-recipient/global email caps in the app are
  # the second line of defense.

  # Top-level declarations (penaltybox + ratecounter live outside subroutines).
  snippet {
    name    = "rate-limit-init"
    type    = "init"
    content = <<-VCL
      penaltybox pb_form_posts {}
      ratecounter rc_form_posts {}
    VCL
  }

  snippet {
    name     = "rate-limit-recv"
    type     = "recv"
    priority = 100
    content  = <<-VCL
      if (req.method == "POST" && req.url.path ~ "^/(events|recover)$") {
        # Already serving time? Reject straight away.
        if (ratelimit.penaltybox_has(pb_form_posts, client.ip)) {
          error 429 "Too Many Requests";
        }
        # Count this request; if the 60s rate is over the limit, jail the IP.
        if (ratelimit.check_rate(client.ip, rc_form_posts, 1, 60, ${var.form_post_rate_limit}, pb_form_posts, ${var.form_post_penalty})) {
          error 429 "Too Many Requests";
        }
      }
    VCL
  }

  # Friendly plain-text body for the 429s raised above.
  snippet {
    name     = "rate-limit-error"
    type     = "error"
    priority = 100
    content  = <<-VCL
      if (obj.status == 429) {
        set obj.http.Content-Type = "text/plain; charset=utf-8";
        set obj.http.Retry-After = "600";
        synthetic "Too many requests. Please slow down and try again shortly." {"\n"};
        return(deliver);
      }
    VCL
  }

  force_destroy = true
}
