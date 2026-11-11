# The current untagged Home LAN is deliberately not redefined as a VLAN.
resource "unifi_network" "vlan" {
  for_each = var.vlan_networks

  name                = each.value.name
  vlan                = each.value.vlan_id
  third_party_gateway = true

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  wifi_passphrases = {
    iot   = var.iot_wifi_password
    guest = var.guest_wifi_password
  }
}

resource "unifi_wlan" "wifi" {
  for_each = var.wifi_networks

  name          = each.value.ssid
  security      = "wpapsk"
  user_group_id = each.value.user_group_id
  network_id = each.value.vlan_key != null ? (
    unifi_network.vlan[each.value.vlan_key].id
  ) : each.value.network_id
  enabled               = each.value.enabled
  l2_isolation          = each.value.client_isolation
  passphrase_wo         = local.wifi_passphrases[each.key]
  passphrase_wo_version = each.value.password_version
  wpa = {
    mode = "wpa2"
    enc  = "ccmp"
  }
  wpa3 = {
    support    = each.value.wpa3_support
    transition = each.value.wpa3_transition
  }
  pmf_mode             = each.value.pmf_mode
  wlan_band            = "both"
  wlan_bands           = ["2g", "5g"]
  ap_group             = { mode = "all", ids = ["5f6d353a60bf0e01805fac9d"] }
  fast_roaming_enabled = false
  hide_ssid            = false
  is_guest             = false

  lifecycle {
    prevent_destroy = true
  }
}
