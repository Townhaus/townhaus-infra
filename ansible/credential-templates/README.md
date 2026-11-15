# Sudo credentials

Create two Login items in the **Homelab** vault:

| Item | Username | Password |
| --- | --- | --- |
| Beelink Sudo | saegey | Existing Beelink sudo/login password |
| Aswitch Sudo | saegey | Existing aswitch sudo/login password |

The service account currently has read access only, so create these with your
personal 1Password account. The JSON files are dummy templates, not credentials.
To import them from the repository root in a locally authenticated shell:

```sh
env -u OP_SERVICE_ACCOUNT_TOKEN op item create --vault Homelab --template ansible/credential-templates/beelink-sudo.json
env -u OP_SERVICE_ACCOUNT_TOKEN op item create --vault Homelab --template ansible/credential-templates/aswitch-sudo.json
```

Replace each item's password in the 1Password app. Do not put real passwords in
these JSON files. Ansible reads the password at runtime; no sudoers changes are
needed. The placeholder causes an error before any sudo login attempt.

After filling the items, verify without deploying:

```sh
ansible townhaus_caddy:aswitch --become -m ansible.builtin.command -a 'id -u'
```

Both hosts should return `0`. No entry is needed for pi-cam, whose existing
passwordless sudo already works. Avoid inventory dumps or debug tasks that print
`ansible_become_password`.
