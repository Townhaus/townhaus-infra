variable "create_camera_network" {
  type        = bool
  default     = false
  description = "Create the proposed third-party camera network; keep enabled after creation."
}

resource "unifi_network" "cameras" {
  count               = var.create_camera_network ? 1 : 0
  name                = "Cameras"
  vlan                = 40
  third_party_gateway = true

  lifecycle {
    prevent_destroy = true
  }
}

variable "manage_camera_port" {
  type        = bool
  default     = false
  description = "Manage first-floor switch port 1 as the camera access port."
}

resource "unifi_device_port" "camera" {
  for_each = var.manage_camera_port ? { camera = true } : {}

  device_mac            = "74:f9:2c:98:1e:a3"
  index                 = 1
  native_networkconf_id = unifi_network.cameras[0].id
  tagged_vlan_mgmt      = "block_all"

  lifecycle {
    prevent_destroy = true
  }
}
