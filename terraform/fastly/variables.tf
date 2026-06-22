variable "service_name" {
  description = "Name of the Fastly delivery service."
  type        = string
  default     = "ruyfo-planner"
}

variable "domain_name" {
  description = "Public hostname the planner is served on (the CNAME you point at Fastly)."
  type        = string
}

variable "backend_address" {
  description = "Origin host running uvicorn (hostname or IP). Should not be publicly reachable except via Fastly — see origin lock-down below."
  type        = string
}

variable "backend_port" {
  description = "Origin port uvicorn listens on (see nixos/ruyfo-planner.nix; default there is 8010)."
  type        = number
  default     = 8010
}

variable "backend_use_ssl" {
  description = "Whether Fastly should connect to the origin over TLS."
  type        = bool
  default     = false
}

# --- Origin lock-down --------------------------------------------------------
# Edge protections are pointless if bots can hit the origin IP directly. Pick
# one (ideally both):
#   1. Firewall the origin to Fastly's published IP ranges
#      (https://api.fastly.com/public-ip-list), or
#   2. Set this shared secret. Fastly stamps it on every origin request as the
#      X-Origin-Secret header; the app must reject requests that lack it.
# Leave empty ("") to skip the header (e.g. if you firewall instead).
variable "origin_shared_secret" {
  description = "Secret stamped on origin requests so the app can reject traffic that bypassed Fastly. Empty to disable."
  type        = string
  default     = ""
  sensitive   = true
}

# --- Edge rate limiting on the unauthenticated form POSTs --------------------
variable "form_post_rate_limit" {
  description = "Max POSTs to /events or /recover allowed per client IP within the 60s window before the penalty box kicks in."
  type        = number
  default     = 30
}

variable "form_post_penalty" {
  description = "How long a client that trips the limit is blocked (VCL RTIME literal, e.g. \"10m\")."
  type        = string
  default     = "10m"
}

# --- Next-Gen WAF / Bot Management (ngwaf.tf only) ---------------------------
variable "ngwaf_corp" {
  description = "Next-Gen WAF corp short name. Only used by ngwaf.tf."
  type        = string
  default     = ""
}

variable "ngwaf_site_short_name" {
  description = "Next-Gen WAF site short name to attach the bot challenge rule to. Only used by ngwaf.tf."
  type        = string
  default     = ""
}

variable "ngwaf_email" {
  description = "Next-Gen WAF API user email. Set via TF_VAR_ngwaf_email. Only used by ngwaf.tf."
  type        = string
  default     = ""
}

variable "ngwaf_auth_token" {
  description = "Next-Gen WAF API token. Set via TF_VAR_ngwaf_auth_token. Only used by ngwaf.tf."
  type        = string
  default     = ""
  sensitive   = true
}
