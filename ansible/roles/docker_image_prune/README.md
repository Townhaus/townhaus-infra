# Weekly Docker image cleanup

The Beelink playbook installs a cron job for the SSH user to prune unused Docker
images every Sunday at 04:30 in the host's timezone. The default `until=168h`
filter keeps images created within the last seven days. Images referenced by
containers are retained. Older images needed for rollback must be pulled again.

The role requires Docker access and an active cron daemon. It only prunes images;
volumes and build cache use separate retention policies.

Apply just this schedule with:

```sh
ansible-playbook -i ansible/inventory.ini ansible/playbooks/docker_image_prune.yml
```

Override `docker_image_prune_schedule` and `docker_image_prune_minimum_age` in host
or group variables to change the schedule or image age threshold.
