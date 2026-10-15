output "tunnel_id" {
  description = "Cloudflare Tunnel ID -- useful when cross-checking against the dashboard or rotating the tunnel token."
  value       = cloudflare_zero_trust_tunnel_cloudflared.inventory.id
}

output "app_hostname" {
  value = var.app_hostname
}
