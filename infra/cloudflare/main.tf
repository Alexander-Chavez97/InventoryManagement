# ---------------------------------------------------------------------------
# Every resource below already exists -- it was created by hand in the
# Cloudflare dashboard (see ../README.md, step 3). Nothing here is meant to
# be `terraform apply`'d cold.
#
# Workflow: `terraform import` each resource first, then `terraform plan`,
# and confirm the plan shows NO changes (or only the exact change you
# intend) before ever running `terraform apply`. See README.md in this
# directory for the exact import commands. If a plan wants to recreate or
# delete something you didn't touch, stop and reconcile the .tf to match
# reality -- don't apply through it.
# ---------------------------------------------------------------------------

resource "cloudflare_zero_trust_tunnel_cloudflared" "inventory" {
  account_id = var.cloudflare_account_id
  name       = var.tunnel_name
  config_src = "cloudflare" # ingress is managed remotely (by the config resource below), not a local config.yml on the host
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "inventory" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.inventory.id

  config = {
    ingress = [
      {
        hostname = var.app_hostname
        service  = "http://nginx:80" # reaches the nginx service inside this host's docker compose network
      },
      # Cloudflare requires a catch-all as the final ingress rule.
      {
        service = "http_status:404"
      }
    ]
  }
}

resource "cloudflare_dns_record" "inventory" {
  zone_id = var.cloudflare_zone_id
  name    = var.app_hostname
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.inventory.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1 # required "Auto" value when proxied = true
}

# As of the v5 provider, Access policies are standalone/reusable objects --
# NOT nested under a specific application via application_id/precedence
# (that's the old v4 shape). An application attaches to a policy the other
# way around, via its own `policies` list below.
resource "cloudflare_zero_trust_access_policy" "admin_email_otp" {
  account_id = var.cloudflare_account_id
  name       = "Admin allowlist -- email OTP"
  decision   = "allow"

  include = [
    for addr in var.admin_allowed_emails : {
      email = {
        email = addr
      }
    }
  ]
}

resource "cloudflare_zero_trust_access_application" "admin" {
  account_id       = var.cloudflare_account_id
  name             = "Inventory Management -- /admin/"
  domain           = "${var.app_hostname}${var.admin_path}"
  type             = "self_hosted"
  session_duration = "24h"
  policies = [
    {
      id         = cloudflare_zero_trust_access_policy.admin_email_otp.id
      precedence = 1
    }
  ]
}
