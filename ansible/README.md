# Deployment with Ansible

Perform automated deployment of SwarmPAL-Processor to a host running Ubuntu 22.04.

## Setup

Launce an Ubuntu 22.04 instance in a cloud provider using a no passphrase ssh keypair.
Add the following section in `~/.ssh/config`:
```
Host swarmpal-dev
    Hostname <server IP or URL>
    User ubuntu
    IdentityFile ~/.ssh/<ssh private key>
    IdentitiesOnly yes
```

In this directory run:
```bash
$ ansible ping -i inventory.yaml -m ping
$ ansible-playbook -i inventory.yaml swarmpal_processor_dev.yaml --diff
```

## TODO

Possible improvements:

  * Create a separate user to keep configuration files.
  * Support different Linux distributions
  * Use GitHub workflows to create the Docker image.
  * Use an upstream Docker image.
