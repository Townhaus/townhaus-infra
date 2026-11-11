# Existing Home Wi-Fi, adopted from the confirmed controller inventory.
# OPNsense owns the untagged 192.168.2.0/24 network; keep that network unmanaged.
resource "unifi_wlan" "home" {
  name          = "GREATWHITE"
  site          = "default"
  security      = "wpapsk"
  network_id    = "5d8fd4c09cd9a20667736257"
  user_group_id = "5d8fd4c09cd9a20667736258"
  enabled       = true

  wpa = {
    mode = "wpa2"
    enc  = "ccmp"
  }
  wpa3 = {
    support    = false
    transition = false
  }
  pmf_mode   = "disabled"
  wlan_band  = "both"
  wlan_bands = ["2g", "5g"]
  ap_group = {
    mode = "all"
    ids  = ["5f6d353a60bf0e01805fac9d"]
  }

  l2_isolation                   = false
  is_guest                       = false
  hide_ssid                      = false
  fast_roaming_enabled           = false
  bss_transition                 = true
  private_preshared_keys_enabled = false

  lifecycle {
    prevent_destroy = true
    # Preserve the current password; do not initialize or rotate it on import.
    ignore_changes = [passphrase]
  }
}

import {
  to = unifi_wlan.home
  id = "5d8fd52e9cd9a20667736268"
}
