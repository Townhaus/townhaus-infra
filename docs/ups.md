# Beelink USB UPS telemetry

The CyberPower CP1500 AVR UPS (USB `0764:0501`) connects to the Beelink.
The `nut_server` role installs Debian's NUT packages and configures the
`usbhid-ups` driver under the name `myups`. It serves readings on localhost
and `192.168.2.151`, TCP 3493. No UPS control credentials are provisioned;
Home Assistant can read telemetry without authentication. No firewall changes
are made. Keep this listener on the trusted LAN.

This is a telemetry-only setup: `nut-monitor.service` is stopped, disabled and
masked. It does not configure automatic shutdown or UPS power-off commands.
The role owns `nut.conf`, `ups.conf` and `upsd.conf`, backing up replaced files.
Defaults can be overridden in host/group variables. The listen address must
already be assigned to the host.

From the repository root, preview and deploy:

```sh
ansible-playbook ansible/playbooks/ups.yml --limit beelink.local --ask-become-pass --check --diff
ansible-playbook ansible/playbooks/ups.yml --limit beelink.local --ask-become-pass
```

On first installation, check mode previews packages but skips configuration
and services that do not yet exist. Actual deployment waits for the LAN
listener and checks that `battery.charge` and `ups.status` are available.
The role reloads Debian's udev rules and reapplies them only to USB devices
matching the configured UPS vendor/product IDs. This gives the NUT driver
access even when the cable was connected before the packages were installed;
no cable replug or host reboot is needed. A failed driver retries automatically.
Running the playbook again reconciles configuration without restarting
unchanged services.

In Home Assistant, open Settings > Devices & services > Network UPS Tools and
reconfigure the existing integration to host `192.168.2.151`, port `3493`,
with username and password blank. Stop the old USB NUT app on the HA host if
it is still running. Reusing the existing integration and UPS name is intended
to retain entity IDs; verify `sensor.myups_battery_charge` and
`sensor.myups_status_data` in Developer Tools > States. Existing dashboards
and notification automations in `home-assistant-config` use those IDs.
Do not delete the old integration before checking whether reconfiguration works.

For troubleshooting on the Beelink:

```sh
upsc myups@192.168.2.151
sudo systemctl status nut-server nut-driver-enumerator --no-pager
sudo journalctl -u nut-server -u 'nut-driver@*' -n 100 --no-pager
```

References: [HA NUT integration](https://www.home-assistant.io/integrations/nut/),
[USB HID driver](https://networkupstools.org/docs/man/usbhid-ups.html),
[driver enumeration](https://networkupstools.org/docs/man/nut-driver-enumerator.html).
