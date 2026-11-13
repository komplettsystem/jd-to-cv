variable "hcloud_token" {
  description = "A Hetzner Cloud project API token with read and write access. Set it with: export TF_VAR_hcloud_token=..."
  type        = string
  sensitive   = true
}

variable "ssh_public_key" {
  description = "The contents of your public SSH key (for example ~/.ssh/id_ed25519.pub)"
  type        = string
}

variable "admin_cidrs" {
  description = "Addresses that may reach SSH, for example [\"203.0.113.7/32\"]. Find yours with: curl -4 ifconfig.me"
  type        = list(string)
}

variable "server_type" {
  description = "An x86 plan with 2 vCPU and at least 2 GB RAM (the app needs about 200 MB, the Docker build and the system the rest). Check the current names with: hcloud server-type list"
  type        = string
  default     = "cpx12"
}

variable "location" {
  description = "fsn1 (Falkenstein) or nbg1 (Nuremberg) in Germany, hel1 (Helsinki) in Finland"
  type        = string
  default     = "fsn1"
}

variable "backups" {
  description = "Daily server backups (about 20 percent extra)"
  type        = bool
  default     = true
}
