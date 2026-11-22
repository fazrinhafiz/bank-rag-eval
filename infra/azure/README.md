# Azure Container Apps deployment (validated, not deployed)

**Status: this has never been applied.** There is no Azure subscription behind this repository. CI runs
`terraform fmt -check` and `terraform validate` on every push, and `tests/test_infra.py` checks the
policies below. That proves the configuration is well-formed and keeps the design rules, not that it deploys.
The first real `plan` and `apply` will surface what validation cannot: quota, region availability,
naming collisions and role-assignment propagation delays.

## What it creates

| resource | why |
|---|---|
| Container Apps environment + app `bankrag-<env>-api` | runs the same image CI built and smoke-tested |
| User-assigned managed identity | pulls the image and reads secrets; no passwords anywhere |
| Container registry (admin user off) | private image store; the identity has `AcrPull` |
| Key Vault (RBAC, purge protection) | holds `bankrag-audit-key` and `bankrag-trace-salt`; the identity has `Key Vault Secrets User` |
| Storage account, Azure Files share `audit` | the SQLite audit log and duplicate registry, mounted at `/app/audit` |
| Blob container `audit-anchors` with an immutability policy | write-once store for `bankrag audit head` exports |
| Log Analytics workspace | the container's JSON logs (request ids included) |

## Design rules the configuration follows

- **One replica.** The audit log is one SQLite file with one writer, so `min_replicas = max_replicas = 1`.
  Scaling out means moving the log to Azure Database for PostgreSQL first (same schema and chain).
- **No WAL on the file share.** SQLite's WAL mode needs shared memory on one host, which a network share
  does not give, so the app runs with `BANKRAG_AUDIT_JOURNAL_MODE=DELETE`.
- **Secrets never enter Terraform state.** The app references Key Vault secrets by URI. Their values are set
  with `az keyvault secret set`. (One exception: the storage account key used for the Azure Files mount is
  in state, because Container Apps mounts shares with the account key. Keep state in a private backend.)
- **Immutable image tags.** `image_tag` refuses `latest`, so each revision is one build that passed the gates.
- **Private by default.** `ingress_external = false` keeps the API inside the environment; an API gateway
  (for example API Management) is the intended front door. HTTP is refused (`allow_insecure_connections = false`).

## Known gaps

- **Revision rollover.** In single-revision mode, Azure starts the new replica before stopping the old one,
  so for a short time two replicas can open the same SQLite file over SMB. SQLite's locking over network
  shares is not fully reliable. The chain fails closed rather than forking (`seq` is the primary key, so
  a second writer's insert is refused and that request returns 500), but the file itself is not protected
  from corruption. This is the strongest reason to move the log to Postgres before production.
- **Anchor export is not scheduled.** The write-once container exists; the job that runs
  `bankrag audit head` and uploads the result (a Container Apps Job on a cron, with `Storage Blob Data
  Contributor` on that container only) is not written.
- **No private endpoints.** Key Vault, the registry and storage are reached over public endpoints with
  identity-based access. A bank deployment would add private endpoints and disable public network access.
- **No lock file committed.** Generate `.terraform.lock.hcl` on the first real `init` and commit it.

## Deploying, when there is a subscription

```bash
cd infra/azure
cp example.tfvars dev.tfvars            # set image_tag to the git SHA CI gated
terraform init                          # after configuring the backend block in versions.tf
terraform apply -var-file=dev.tfvars -target=azurerm_container_registry.main -target=azurerm_key_vault.main
az acr build -r <registry> -t bankrag:<git sha> ../..        # or push the image CI built
az keyvault secret set --vault-name <vault> --name bankrag-audit-key  --value "$(openssl rand -hex 32)"
az keyvault secret set --vault-name <vault> --name bankrag-trace-salt --value "$(openssl rand -hex 32)"
terraform apply -var-file=dev.tfvars
```

The two-step apply exists because the app's first revision needs the image and both secrets to exist.
