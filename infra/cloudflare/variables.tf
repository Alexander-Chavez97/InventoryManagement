variable "cloudflare_api_token" {
  description = <<-EOT
    Cloudflare API token, scoped to exactly what this config touches:
      Zone (meridiansupply.example) -> Zone:Read, DNS:Edit
      Account                -> Cloudflare Tunnel:Edit, Access: Apps and Policies:Edit
    Create it under My Profile -> API Tokens -> Create Token -> Custom Token.
    Never commit it. Pass it as TF_VAR_cloudflare_api_token in the shell
    environment, not in terraform.tfvars.
  EOT
  type      = string
  sensitive = true
}

variable "cloudflare_account_id" {
  description = "Account ID -- right-hand sidebar of any domain overview page in the dashboard."
  type        = string
}

variable "cloudflare_zone_id" {
  description = "Zone ID for meridiansupply.example -- same sidebar as the account ID."
  type        = string
}

variable "tunnel_name" {
  description = "Name of the existing Cloudflare Tunnel to import, exactly as shown in Zero Trust -> Networks -> Tunnels."
  type        = string
  default     = "inventory-mgmt-prod"
}

variable "app_hostname" {
  description = "Public hostname the tunnel routes to the production nginx service."
  type        = string
  default     = "inventory.meridiansupply.example"
}

variable "admin_path" {
  description = "Path locked behind the Access policy."
  type        = string
  default     = "/admin/"
}

variable "admin_allowed_emails" {
  description = "Emails allowed through the /admin/ email-OTP Access policy. Keep this scoped to who actually needs admin access -- don't widen it to a whole domain."
  type        = list(string)
}
