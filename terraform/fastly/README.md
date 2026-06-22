# Fastly edge config for the RUYFO planner

Terraform that puts the planner behind Fastly and shuts down the bot abuse of
the two unauthenticated form endpoints (`POST /events`, `POST /recover`), which
otherwise let anyone make the app send mail to an arbitrary address.

Three layers, outermost first:

1. **Bot challenge** (`ngwaf.tf`) — an invisible JS proof-of-work browser
   challenge on the two POSTs via Next-Gen WAF / Bot Management. Stops
   headless/scripted bots before they reach the origin. *Needs the NGWAF + Bot
   Management entitlement.*
2. **Edge rate limiting** (`main.tf`) — per-client-IP cap on those POSTs using a
   penalty box. Works on the base CDN, no add-on. This is the backstop if a bot
   ever solves the challenge.
3. **Origin caps** (in the app, already shipped) — global + per-recipient daily
   email caps in `app/mailcap.py`. The last line of defense, independent of the
   edge.

## Origin lock-down (required)

Layers 1–2 are worthless if a bot can reach the origin IP directly. Do at least
one of:

- **Firewall the origin to [Fastly's IP ranges](https://api.fastly.com/public-ip-list).**
  The NixOS module exposes `host`/`openFirewall` knobs for this.
- **Set `origin_shared_secret`.** Fastly then stamps `X-Origin-Secret` on every
  origin request, and the app rejects (403) anything lacking it. Wire the same
  value into the app via `RUYFO_ORIGIN_SECRET` (or `RUYFO_ORIGIN_SECRET_FILE`,
  or the NixOS module's `originSecretFile` option). This is the right choice
  when the origin host also serves other sites and can't be IP-firewalled.

## Usage

```bash
cd terraform/fastly
cp terraform.tfvars.example terraform.tfvars   # fill in
export FASTLY_API_KEY=...                       # and SIGSCI_EMAIL/SIGSCI_TOKEN for ngwaf.tf
terraform init
terraform plan
terraform apply
```

Use an **encrypted remote backend** — `origin_shared_secret` is stored in state.

If you haven't enabled NGWAF yet, leave `ngwaf_site_short_name` empty; the
challenge rule is `count`-gated off and the rate limiting in `main.tf` applies on
its own.

## Tuning

| Variable | Default | What |
|----------|---------|------|
| `form_post_rate_limit` | `30` | POSTs to the two paths per client IP per 60s before the penalty box |
| `form_post_penalty` | `10m` | how long a tripped IP stays blocked |
| `allow_interactive` (in `ngwaf.tf`) | `false` | `false` = invisible JS challenge; `true` = interactive CAPTCHA |
