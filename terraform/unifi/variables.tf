variable "controller_url" {
  description = "Controller origin, without /api or other paths. Trust Caddy's CA on the Terraform machine."
  type        = string
  default     = "https://unifi-admin.home.arpa"
}

variable "site" {
  description = "Internal site identifier; verify it after the OS Server migration."
  type        = string
  default     = "default"
}

variable "allow_insecure" {
  description = "Skip controller TLS verification only when explicitly needed for local bootstrap."
  type        = bool
  default     = false
}

variable "vlan_networks" {
  description = "UniFi VLAN-only networks matching the configured OPNsense VLANs. OPNsense owns routing, DHCP and firewall policy."
  type = map(object({
    name    = string
    vlan_id = number
  }))
  default = {
    iot   = { name = "IoT", vlan_id = 20 }
    guest = { name = "Guest", vlan_id = 30 }
  }

  validation {
    condition = alltrue([
      for network in values(var.vlan_networks) :
      network.vlan_id >= 2 && network.vlan_id <= 4094 && floor(network.vlan_id) == network.vlan_id
    ]) && length(distinct([for network in values(var.vlan_networks) : network.vlan_id])) == length(var.vlan_networks)
    error_message = "VLAN IDs must be unique integers between 2 and 4094."
  }
}

variable "wifi_networks" {
  description = "SSIDs to manage. Import existing SSIDs before applying. network_id is the controller network ID (or use vlan_key for a managed VLAN)."
  type = map(object({
    ssid             = string
    user_group_id    = string
    network_id       = optional(string)
    vlan_key         = optional(string)
    enabled          = optional(bool, true)
    password_version = optional(number, 1)
    client_isolation = optional(bool)
    wpa3_support     = optional(bool, false)
    wpa3_transition  = optional(bool, false)
    pmf_mode         = optional(string, "disabled")
  }))
  default = {
    iot = {
      ssid             = "GREATWHITE-IoT"
      user_group_id    = "5d8fd4c09cd9a20667736258"
      vlan_key         = "iot"
      client_isolation = false
    }
    guest = {
      ssid             = "GREATWHITE-Guest"
      user_group_id    = "5d8fd4c09cd9a20667736258"
      vlan_key         = "guest"
      client_isolation = true
      wpa3_support     = true
      wpa3_transition  = true
      pmf_mode         = "optional"
    }
  }

  validation {
    condition = alltrue([
      for wifi in values(var.wifi_networks) :
      (wifi.network_id != null) != (wifi.vlan_key != null)
    ])
    error_message = "Each SSID must specify exactly one of network_id or vlan_key."
  }
}

variable "iot_wifi_password" {
  description = "GREATWHITE-IoT password, injected by op run from 1Password."
  type        = string
  sensitive   = true
  ephemeral   = true

  validation {
    condition     = length(var.iot_wifi_password) >= 8 && length(var.iot_wifi_password) <= 63
    error_message = "IoT Wi-Fi password must have 8–63 characters."
  }
}

variable "guest_wifi_password" {
  description = "GREATWHITE-Guest password, injected by op run from 1Password."
  type        = string
  sensitive   = true
  ephemeral   = true

  validation {
    condition     = length(var.guest_wifi_password) >= 8 && length(var.guest_wifi_password) <= 63
    error_message = "Guest Wi-Fi password must have 8–63 characters."
  }
}
