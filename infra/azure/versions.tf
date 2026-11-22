terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.14"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State holds the storage account key used for the Azure Files mount, so it belongs in a private,
  # access-controlled backend, never in git. Uncomment and fill in before the first real `apply`.
  # CI runs `terraform init -backend=false`, so this block is not needed for validation.
  #
  # backend "azurerm" {
  #   resource_group_name  = "tfstate-rg"
  #   storage_account_name = "<unique tfstate account>"
  #   container_name       = "tfstate"
  #   key                  = "bankrag.tfstate"
  #   use_azuread_auth     = true
  # }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = false
    }
  }
}
