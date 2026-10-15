# Cloudflare config, as code

Codifies the three Cloudflare-managed pieces that currently only exist
because someone clicked through the dashboard (see `../README.md`, step 3):
the Tunnel, the public-hostname routing on it, the DNS record that points
`inventory.novastartx.com` at the tunnel, and the Zero Trust Access policy
locking `/admin/` behind email-OTP.

**This does not stand up a new tunnel.** The tunnel, DNS record, and Access
app already exist in production. The whole point here is to bring
already-real infrastructure under version control without touching it, so
that from now on a change to ingress routing or the admin allowlist is a
diffable, reviewable `.tf` edit instead of an unlogged dashboard click --
and so the next person inherits a description of what's there, not just
a login to go find out.

Terraform (and Ansible, for that matter) otherwise has little to do on this
project: there's one Windows host, provisioned once by `infra/bootstrap.ps1`,
running a docker-compose stack that's already its own app-layer IaC. Cloudflare
is the one place where real config lives outside any file in this repo.

## Prerequisites

- Terraform >= 1.5 (run from your laptop or the Mini PC -- this only talks
  to Cloudflare's API, not to anything on the host itself).
- A scoped Cloudflare API token (My Profile -> API Tokens -> Create Token ->
  Custom Token): Zone:Read + DNS:Edit on the `novastartx.com` zone, and
  Account-level Cloudflare Tunnel:Edit + Access: Apps and Policies:Edit.
  **Don't paste the token into chat or commit it anywhere** -- export it as
  `TF_VAR_cloudflare_api_token` in the shell you run terraform from.

## One-time import

Terraform only manages what it's told to track. Point it at the resources
that already exist before running anything else, in this order:

```powershell
cd infra/cloudflare
cp terraform.tfvars.example terraform.tfvars   # then fill in account_id, zone_id, admin_allowed_emails -- NOT the token
$env:TF_VAR_cloudflare_api_token = "paste-the-token-here-only-in-your-own-shell"
terraform init
```

1. **Tunnel** -- Zero Trust -> Networks -> Tunnels -> click the tunnel ->
   its ID is in the URL and on the Overview tab.
   ```powershell
   terraform import cloudflare_zero_trust_tunnel_cloudflared.inventory <account_id>/<tunnel_id>
   terraform import cloudflare_zero_trust_tunnel_cloudflared_config.inventory <account_id>/<tunnel_id>
   ```

2. **DNS record** -- the dashboard's "Edit DNS record" panel doesn't show the
   record's ID anywhere, so ask the API for it instead (same token you're
   already using):
   ```powershell
   $headers = @{ Authorization = "Bearer $env:TF_VAR_cloudflare_api_token" }
   (Invoke-RestMethod -Headers $headers "https://api.cloudflare.com/client/v4/zones/<zone_id>/dns_records?name=inventory.novastartx.com").result
   ```
   That prints the record object -- copy its `id` field.
   ```powershell
   terraform import cloudflare_dns_record.inventory <zone_id>/<record_id>
   ```

3. **Access application + policy** -- Zero Trust -> Access -> Applications ->
   click the app -> app ID is in the URL; its policy is nested under it.
   ```powershell
   terraform import cloudflare_zero_trust_access_application.admin <account_id>/<app_id>
   terraform import cloudflare_zero_trust_access_policy.admin_email_otp <account_id>/<app_id>/<policy_id>
   ```

4. **Before touching anything else:**
   ```powershell
   terraform plan
   ```
   This must come back with **no changes**, or only the exact change you
   meant to make. If it wants to recreate or delete something you didn't
   touch, stop -- edit the `.tf` to match what's actually there rather than
   applying through the mismatch. Never run `terraform apply` on the first
   plan after an import without reading it line by line.

## Day to day

Once imported, change ingress routing, the hostname, or who's on the
`/admin/` allowlist by editing `main.tf` / `terraform.tfvars` and running
`terraform plan` then `terraform apply` -- not by clicking in the dashboard.
A dashboard click will just show up as drift on the next `plan`.

## A note on resource names

The Cloudflare Terraform provider has renamed several resources across
major versions (e.g. `cloudflare_tunnel` -> `cloudflare_zero_trust_tunnel_cloudflared`,
`cloudflare_record` -> `cloudflare_dns_record`, `cloudflare_access_application`
-> `cloudflare_zero_trust_access_application`). This config targets the `~> 5.0`
provider line pinned in `versions.tf`. If `terraform init` or `plan` complains
about an unknown resource type, check the installed provider's actual version
and cross-reference the Terraform Registry docs for `cloudflare/cloudflare`
before renaming anything here.

## Out of scope, on purpose

The buildathon demo stack's own tunnel (`mcp.novastartx.com`) isn't part of
this config -- it's a separate, disposable stack on a different machine, not
the production system this is meant to make hand-off-able.
