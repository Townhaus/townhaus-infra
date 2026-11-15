# OPNsense traffic inspection

On 2026-11-11, Suricata was enabled in PCAP (alert-only) mode on LAN,
IoT, Guest, and Cameras. The ET Open `emerging-exploit` and
`emerging-malware` rulesets and the OPNsense test ruleset were enabled and
downloaded. Rules update on the IDS schedule created by OPNsense. Review
events under **Services > Intrusion Detection > Alerts** and tune noisy rules
before considering prevention. This only sees traffic routed through the
firewall, not traffic switched within a VLAN.

The service reported running and the API showed enabled malware and test
signatures. A test download from the management machine did not produce a
matching alert; that machine's traffic path was not verified. From a client
on each monitored network, request `http://pkg.opnsense.org/test/eicar.com.txt`
and confirm the OPNsense test alert appears. The file is the standard harmless
antivirus test string. Check connectivity, CPU, memory and throughput after
enabling inspection; a full per-VLAN and throughput test remains outstanding.

After the update to OPNsense 26.7.9, `os-sunnyvalley` and `os-sensei` 2.7.2
were installed on 2026-11-12. Zenarmor's initial configuration wizard still
needs to be completed using free features and no TLS decryption. Check hardware
and interface compatibility in the wizard before choosing a mode, especially
because LAN and WAN use USB Ethernet interfaces. Do not assume Zenarmor is
inspecting traffic until its configuration and reports have been verified.
During the wizard, the user selected passive mode for IoT, Guest and Cameras;
Home LAN (`ue1`) did not appear in the interface list. OPNsense reports `ue1`
enabled and up. Its absence from Zenarmor remains unresolved, so do not assume
Home LAN traffic is covered by Zenarmor. Suricata remains configured on LAN.

Do not enable inline IPS until out-of-band console access is available and
tested. Verify offload and VLAN/driver compatibility, baseline connectivity,
and tune alerts first. Introduce a small set of drop rules and test recovery.
For rollback of current passive monitoring, disable IDS under **Services >
Intrusion Detection > Administration** and apply. A pre-change configuration
backup is stored in the ignored `backups/network` directory; treat it as secret.

References: [OPNsense IDS/IPS](https://docs.opnsense.org/manual/ips.html),
[netmap precautions](https://docs.opnsense.org/troubleshooting/network.html),
and [Zenarmor installation](https://docs.opnsense.org/vendor/sunnyvalley/zenarmor_install.html).
