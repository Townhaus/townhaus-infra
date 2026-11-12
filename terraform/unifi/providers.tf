# Authentication comes from UNIFI_USERNAME/UNIFI_PASSWORD or UNIFI_API_KEY.
# Use a dedicated local account; never put credentials in tracked files.
provider "unifi" {
  api_url        = var.controller_url
  site           = var.site
  allow_insecure = var.allow_insecure
}
