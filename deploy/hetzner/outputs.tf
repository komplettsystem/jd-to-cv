output "ipv4" {
  description = "Point an A record for your domain at this address"
  value       = hcloud_server.app.ipv4_address
}

output "ipv6" {
  value = hcloud_server.app.ipv6_address
}

output "ssh" {
  value = "ssh root@${hcloud_server.app.ipv4_address}"
}
