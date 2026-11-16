# Keep the address used by Home Assistant's existing Bravia integration.
resource "opnsense_dnsmasq_host" "sony_tv" {
  hostname           = "sony-tv"
  ip_addresses       = ["192.168.2.7"]
  hardware_addresses = ["52:e0:35:34:b8:2b"]
  description        = "Townhaus Sony Bravia TV LAN DHCP reservation"
  is_ignored         = false
}
