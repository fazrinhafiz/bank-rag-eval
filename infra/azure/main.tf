# bankrag on Azure Container Apps. Written and validated in CI (`terraform validate`), never applied:
# there is no Azure subscription behind this repository. See infra/azure/README.md.
#
# Design, mapped from the container and the audit log's constraints:
#   - one replica (min = max = 1): the audit log and duplicate registry are one SQLite file with one writer
#   - the audit file lives on an Azure Files share mounted at /app/audit, journal mode DELETE (no WAL on SMB)
#   - secrets come from Key Vault through a managed identity; Terraform never sees their values
#   - the image is pulled from a private registry with that identity (no admin user, no password)
#   - audit anchors (`bankrag audit head`) go to a blob container with a time-based immutability policy

locals {
  name = "${var.prefix}-${var.environment}"
  # Storage account and registry names: globally unique, lowercase alphanumeric only.
  compact = "${var.prefix}${var.environment}${random_string.suffix.result}"

  tags = merge({
    app         = "bankrag"
    environment = var.environment
    managed_by  = "terraform"
  }, var.tags)

  container_port = 8000
  audit_mount    = "/app/audit"
}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "main" {
  name     = "${local.name}-rg"
  location = var.location
  tags     = local.tags
}

# ------------------------------------------------------------------------------------------------ logs
resource "azurerm_log_analytics_workspace" "main" {
  name                = "${local.name}-logs"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

# ------------------------------------------------------------------------------------------- identity
resource "azurerm_user_assigned_identity" "app" {
  name                = "${local.name}-id"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

# ------------------------------------------------------------------------------------------- registry
resource "azurerm_container_registry" "main" {
  name                = substr(local.compact, 0, 50)
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# -------------------------------------------------------------------------------------------- secrets
# Values are set out of band, so they never enter Terraform state:
#   az keyvault secret set --vault-name <vault> --name bankrag-audit-key  --value "$(openssl rand -hex 32)"
#   az keyvault secret set --vault-name <vault> --name bankrag-trace-salt --value "$(openssl rand -hex 32)"
resource "azurerm_key_vault" "main" {
  name                       = substr("${local.compact}kv", 0, 24)
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  enable_rbac_authorization  = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 90
  tags                       = local.tags
}

resource "azurerm_role_assignment" "kv_secrets_user" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

# -------------------------------------------------------------------------------------------- storage
resource "azurerm_storage_account" "main" {
  name                            = substr(local.compact, 0, 24)
  location                        = azurerm_resource_group.main.location
  resource_group_name             = azurerm_resource_group.main.name
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  # Container Apps mounts Azure Files with the account key, so shared keys stay enabled.
  shared_access_key_enabled = true
  tags                      = local.tags

  blob_properties {
    versioning_enabled = true
  }
}

# The SQLite audit database and duplicate registry.
resource "azurerm_storage_share" "audit" {
  name               = "audit"
  storage_account_id = azurerm_storage_account.main.id
  quota              = 5
}

# Exported (seq, hash) anchors: once written, a blob cannot be changed or deleted for the retention period,
# so cutting events off the end of the log is detectable. Left unlocked so the period can still be
# corrected; lock it (irreversible) once the retention period is agreed.
resource "azurerm_storage_container" "anchors" {
  name                  = "audit-anchors"
  storage_account_id    = azurerm_storage_account.main.id
  container_access_type = "private"
}

resource "azurerm_storage_container_immutability_policy" "anchors" {
  storage_container_resource_manager_id = azurerm_storage_container.anchors.id
  immutability_period_in_days           = var.anchor_retention_days
  protected_append_writes_enabled       = true
  locked                                = false
}

# ------------------------------------------------------------------------------------ container apps
resource "azurerm_container_app_environment" "main" {
  name                       = "${local.name}-env"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  infrastructure_subnet_id   = var.infrastructure_subnet_id
  tags                       = local.tags
}

resource "azurerm_container_app_environment_storage" "audit" {
  name                         = "audit"
  container_app_environment_id = azurerm_container_app_environment.main.id
  account_name                 = azurerm_storage_account.main.name
  share_name                   = azurerm_storage_share.audit.name
  access_key                   = azurerm_storage_account.main.primary_access_key
  access_mode                  = "ReadWrite"
}

resource "azurerm_container_app" "api" {
  name                         = "${local.name}-api"
  container_app_environment_id = azurerm_container_app_environment.main.id
  resource_group_name          = azurerm_resource_group.main.name
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  secret {
    name                = "audit-key"
    identity            = azurerm_user_assigned_identity.app.id
    key_vault_secret_id = "${azurerm_key_vault.main.vault_uri}secrets/bankrag-audit-key"
  }

  secret {
    name                = "trace-salt"
    identity            = azurerm_user_assigned_identity.app.id
    key_vault_secret_id = "${azurerm_key_vault.main.vault_uri}secrets/bankrag-trace-salt"
  }

  ingress {
    external_enabled           = var.ingress_external
    target_port                = local.container_port
    transport                  = "http"
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }

    dynamic "ip_security_restriction" {
      for_each = var.allowed_ip_ranges
      content {
        name             = "allow-${ip_security_restriction.key}"
        action           = "Allow"
        ip_address_range = ip_security_restriction.value
      }
    }
  }

  template {
    # One writer for the SQLite audit log. Scaling out needs the log moved to Postgres first (README).
    min_replicas = 1
    max_replicas = 1

    volume {
      name         = "audit"
      storage_name = azurerm_container_app_environment_storage.audit.name
      storage_type = "AzureFile"
    }

    container {
      name   = "api"
      image  = "${azurerm_container_registry.main.login_server}/bankrag:${var.image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name        = "BANKRAG_AUDIT_KEY"
        secret_name = "audit-key"
      }
      env {
        name        = "BANKRAG_TRACE_SALT"
        secret_name = "trace-salt"
      }
      env {
        name  = "BANKRAG_AUDIT_JOURNAL_MODE"
        value = "DELETE"
      }

      volume_mounts {
        name = "audit"
        path = local.audit_mount
      }

      liveness_probe {
        transport = "HTTP"
        port      = local.container_port
        path      = "/health"
      }

      readiness_probe {
        transport = "HTTP"
        port      = local.container_port
        path      = "/health"
      }
    }
  }

  # The identity must be able to pull the image and read the secrets before the first revision starts.
  depends_on = [azurerm_role_assignment.acr_pull, azurerm_role_assignment.kv_secrets_user]
}
