# One small Hetzner Cloud server with a firewall. Nothing here is applied until the owner runs `terraform apply`.
terraform {
  required_version = ">= 1.6"
  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.48"
    }
  }
}

provider "hcloud" {
  token = var.hcloud_token
}

resource "hcloud_ssh_key" "deploy" {
  name       = "reverse-apply"
  public_key = var.ssh_public_key
}

resource "hcloud_firewall" "web" {
  name = "reverse-apply"

  rule {
    description = "SSH from the administrator only"
    direction   = "in"
    protocol    = "tcp"
    port        = "22"
    source_ips  = var.admin_cidrs
  }
  rule {
    description = "HTTP (redirects to HTTPS, and certificate checks)"
    direction   = "in"
    protocol    = "tcp"
    port        = "80"
    source_ips  = ["0.0.0.0/0", "::/0"]
  }
  rule {
    description = "HTTPS"
    direction   = "in"
    protocol    = "tcp"
    port        = "443"
    source_ips  = ["0.0.0.0/0", "::/0"]
  }
}

resource "hcloud_server" "app" {
  name         = "reverse-apply"
  server_type  = var.server_type
  image        = "ubuntu-24.04"
  location     = var.location
  ssh_keys     = [hcloud_ssh_key.deploy.id]
  firewall_ids = [hcloud_firewall.web.id]
  backups      = var.backups
  user_data    = file("${path.module}/cloud-init.yaml")

  labels = { app = "reverse-apply" }
}
