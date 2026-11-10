#!/usr/bin/env just --justfile

[group('help')]
[private]
default:
    @ just --list --list-heading $'justfile manual page:\n'

# show help
[group('help')]
help: default

# install ansible galaxy collections and roles
[group('install')]
galaxy-install:
    ansible-galaxy collection install -r requirements.yaml
    ansible-galaxy install -r requirements.yaml -p external_roles

# run yamllint
[group('style')]
yamllint *args:
    yamllint {{ args }} .

# run yamlfmt
[group('style')]
yamlfmt *args:
    yamlfmt {{ args }} '**/*.yaml' '**/*.yml'

# dry-run yamlfmt
[group('style')]
yamlfmt-check:
    yamlfmt -dry '**/*.yaml' '**/*.yml'

# run ansible-lint
[group('style')]
ansible-lint *args:
    ansible-lint {{ args }}

# run ansible-lint with auto-fix
[group('style')]
ansible-fix *args:
    ansible-lint --fix {{ args }}

# run all linters
[group('style')]
lint: yamllint ansible-lint

# run all formatters
[group('style')]
fmt: yamlfmt ansible-fix

# syntax check all playbooks without executing
[group('style')]
check:
    ansible-playbook --syntax-check site.yaml
    ansible-playbook --syntax-check base_setup.yaml
    ansible-playbook --syntax-check containers.yaml
    ansible-playbook --syntax-check apps.yaml
    ansible-playbook --syntax-check validation.yaml

# encrypt a file with ansible-vault
[group('vault')]
vault-encrypt *files:
    ansible-vault encrypt {{ files }}

# decrypt a file with ansible-vault
[group('vault')]
vault-decrypt *files:
    ansible-vault decrypt {{ files }}

# edit an encrypted file in place
[group('vault')]
vault-edit file:
    ansible-vault edit {{ file }}

# view an encrypted file without decrypting on disk
[group('vault')]
vault-view file:
    ansible-vault view {{ file }}

# run a playbook with vault password prompt
[group('run')]
play playbook *args:
    ansible-playbook {{ playbook }} --ask-vault-pass {{ args }}

# run site.yaml
[group('run')]
deploy *args:
    ansible-playbook site.yaml --ask-vault-pass {{ args }}

# dry-run site.yaml
[group('run')]
deploy-check *args:
    ansible-playbook site.yaml --check --diff --ask-vault-pass {{ args }}

# run only base setup
[group('run')]
setup *args:
    ansible-playbook base_setup.yaml --ask-vault-pass {{ args }}

# run only app deployment
[group('run')]
apps *args:
    ansible-playbook apps.yaml --ask-vault-pass {{ args }}

# run only post-deployment validation
[group('run')]
validate *args:
    ansible-playbook validation.yaml {{ args }}
